from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import (
    ColumnTransformer,
    TransformedTargetRegressor,
)
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import (
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.metrics import (
    mean_absolute_error,
    mean_squared_error,
    median_absolute_error,
    r2_score,
)
from sklearn.model_selection import (
    train_test_split,
)
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import (
    OneHotEncoder,
)

from app.supabase_client import get_supabase


TRAINING_VIEW = "proxy_training_data"
TARGET_COLUMN = "target_proxy_value"

RANDOM_STATE = 42
TEST_SIZE = 0.20
MAX_TARGET_VALUE = 5_000_000
PAGE_SIZE = 1000

BACKEND_DIR = Path(__file__).resolve().parents[1]

CURRENT_ARTIFACT_DIR = (
    BACKEND_DIR
    / "app"
    / "ml"
    / "artifacts"
)

OUTPUT_DIR = (
    BACKEND_DIR
    / "app"
    / "ml"
    / "candidate_no_listing_data"
)

OUTPUT_DIR.mkdir(
    parents=True,
    exist_ok=True,
)

NUMERIC_FEATURES = [
    "gis_acres",
    "total_acres",
    "lot_size_sqft",
    "latitude",
    "longitude",
    "tax_year",

    "hpi_index",
    "hpi_yoy_change_pct",
    "hpi_period_change_pct",

    "mortgage_rate",
    "mortgage_rate_4week_avg",
    "mortgage_rate_13week_avg",
    "mortgage_rate_change_52week",

    "unemployment_rate",
    "unemployment_rate_3month_avg",
    "unemployment_rate_12month_avg",
    "unemployment_pressure_score",
]

CATEGORICAL_FEATURES = [
    "site_city",
    "site_state",
    "site_zip_code",
    "county_name",
    "property_type",
    "property_type_group",
    "model_segment",
    "is_residential",
]

FEATURE_COLUMNS = (
    NUMERIC_FEATURES
    + CATEGORICAL_FEATURES
)

REALTOR_FEATURES = [
    "median_listing_price",
    "active_listing_count",
    "median_days_on_market",
    "new_listing_count",
    "pending_listing_count",
    "price_reduced_count",
    "median_listing_price_per_square_foot",
    "price_reduction_share",
    "pending_to_active_ratio",
    "market_heat_score",
]

SELECT_COLUMNS = [
    "parcel_id",
    *FEATURE_COLUMNS,
    TARGET_COLUMN,
]


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def fetch_training_data() -> pd.DataFrame:
    client = get_supabase()

    rows: List[Dict[str, Any]] = []
    offset = 0

    select_expression = ",".join(
        SELECT_COLUMNS
    )

    while True:
        result = (
            client
            .table(TRAINING_VIEW)
            .select(select_expression)
            .order("parcel_id")
            .range(
                offset,
                offset + PAGE_SIZE - 1,
            )
            .execute()
        )

        page = result.data or []
        rows.extend(page)

        if (
            len(rows) % 10000 == 0
            and rows
        ):
            print(
                f"  Downloaded "
                f"{len(rows):,} training rows"
            )

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    if not rows:
        raise RuntimeError(
            "No training rows were loaded "
            "from Supabase."
        )

    frame = pd.DataFrame(rows)

    duplicate_ids = (
        frame["parcel_id"]
        .astype("string")
        .duplicated(
            keep=False
        )
    )

    if duplicate_ids.any():
        duplicates = (
            frame.loc[
                duplicate_ids,
                "parcel_id",
            ]
            .astype("string")
            .head(20)
            .tolist()
        )

        raise RuntimeError(
            "Training data contains duplicated "
            f"parcel IDs: {duplicates}"
        )

    return frame


def clean_training_data(
    raw: pd.DataFrame,
) -> pd.DataFrame:
    missing_columns = [
        column
        for column in SELECT_COLUMNS
        if column not in raw.columns
    ]

    if missing_columns:
        raise RuntimeError(
            "Training view is missing required "
            f"columns: {missing_columns}"
        )

    forbidden_columns = [
        column
        for column in REALTOR_FEATURES
        if column in FEATURE_COLUMNS
    ]

    if forbidden_columns:
        raise RuntimeError(
            "Realtor.com features were included "
            f"in FEATURE_COLUMNS: {forbidden_columns}"
        )

    clean = raw.copy()

    clean[TARGET_COLUMN] = (
        pd.to_numeric(
            clean[TARGET_COLUMN],
            errors="coerce",
        )
    )

    clean = clean.loc[
        clean[TARGET_COLUMN].notna()
        & (
            clean[TARGET_COLUMN]
            > 0
        )
    ].copy()

    clean = clean.loc[
        clean[TARGET_COLUMN]
        <= MAX_TARGET_VALUE
    ].copy()

    for column in NUMERIC_FEATURES:
        clean[column] = pd.to_numeric(
            clean[column],
            errors="coerce",
        )

    for column in CATEGORICAL_FEATURES:
        clean[column] = (
            clean[column]
            .astype("string")
            .fillna("missing")
        )

    if clean.empty:
        raise RuntimeError(
            "No training rows remained after "
            "target cleaning."
        )

    if len(clean) < 1000:
        raise RuntimeError(
            "Too few training rows remained: "
            f"{len(clean):,}"
        )

    return clean


def make_one_hot_encoder():
    try:
        return OneHotEncoder(
            handle_unknown="ignore",
            sparse_output=True,
        )
    except TypeError:
        return OneHotEncoder(
            handle_unknown="ignore",
            sparse=True,
        )


def build_model():
    numeric_transformer = Pipeline(
        steps=[
            (
                "imputer",
                SimpleImputer(
                    strategy="median"
                ),
            ),
        ]
    )

    categorical_transformer = Pipeline(
        steps=[
            (
                "imputer",
                SimpleImputer(
                    strategy="most_frequent"
                ),
            ),
            (
                "onehot",
                make_one_hot_encoder(),
            ),
        ]
    )

    preprocessor = ColumnTransformer(
        transformers=[
            (
                "num",
                numeric_transformer,
                NUMERIC_FEATURES,
            ),
            (
                "cat",
                categorical_transformer,
                CATEGORICAL_FEATURES,
            ),
        ],
        remainder="drop",
    )

    random_forest = (
        RandomForestRegressor(
            n_estimators=250,
            max_depth=22,
            min_samples_leaf=3,
            max_features="sqrt",
            random_state=RANDOM_STATE,
            n_jobs=-1,
        )
    )

    pipeline = Pipeline(
        steps=[
            (
                "preprocessor",
                preprocessor,
            ),
            (
                "model",
                random_forest,
            ),
        ]
    )

    return TransformedTargetRegressor(
        regressor=pipeline,
        func=np.log1p,
        inverse_func=np.expm1,
    )


def safe_mape(
    y_true,
    y_pred,
) -> float:
    actual = np.asarray(
        y_true,
        dtype=float,
    )
    predicted = np.asarray(
        y_pred,
        dtype=float,
    )

    mask = actual != 0

    if mask.sum() == 0:
        return float("nan")

    return float(
        np.mean(
            np.abs(
                (
                    actual[mask]
                    - predicted[mask]
                )
                / actual[mask]
            )
        )
        * 100
    )


def percent_within(
    y_true,
    y_pred,
    percentage: float,
) -> float:
    actual = np.asarray(
        y_true,
        dtype=float,
    )
    predicted = np.asarray(
        y_pred,
        dtype=float,
    )

    mask = actual != 0

    if mask.sum() == 0:
        return float("nan")

    relative_error = np.abs(
        actual[mask]
        - predicted[mask]
    ) / actual[mask]

    return float(
        (
            relative_error
            <= percentage
        ).mean()
        * 100
    )


def evaluate_predictions(
    y_true,
    y_pred,
    label: str,
) -> Dict[str, Any]:
    return {
        "model": label,
        "row_count": int(
            len(y_true)
        ),
        "mae": float(
            mean_absolute_error(
                y_true,
                y_pred,
            )
        ),
        "median_absolute_error": float(
            median_absolute_error(
                y_true,
                y_pred,
            )
        ),
        "rmse": float(
            np.sqrt(
                mean_squared_error(
                    y_true,
                    y_pred,
                )
            )
        ),
        "r2": float(
            r2_score(
                y_true,
                y_pred,
            )
        ),
        "mape_pct": safe_mape(
            y_true,
            y_pred,
        ),
        "within_10_pct": percent_within(
            y_true,
            y_pred,
            0.10,
        ),
        "within_15_pct": percent_within(
            y_true,
            y_pred,
            0.15,
        ),
        "within_20_pct": percent_within(
            y_true,
            y_pred,
            0.20,
        ),
    }


def load_current_metrics() -> Dict[
    str,
    Any,
]:
    path = (
        CURRENT_ARTIFACT_DIR
        / "model_metrics.json"
    )

    if not path.exists():
        return {}

    with path.open(
        encoding="utf-8"
    ) as source:
        return json.load(source)


def validate_candidate_metrics(
    candidate: Dict[str, Any],
    current: Dict[str, Any],
) -> Dict[str, Any]:
    current_model = (
        current.get(
            "model_metrics",
            {},
        )
    )

    if not current_model:
        return {
            "passed": True,
            "comparison_available": False,
            "checks": {},
        }

    checks = {
        "mae_within_10_percent": (
            candidate["mae"]
            <= current_model["mae"] * 1.10
        ),
        "r2_within_0_05": (
            candidate["r2"]
            >= current_model["r2"] - 0.05
        ),
        "within_20_pct_within_5_points": (
            candidate["within_20_pct"]
            >= (
                current_model[
                    "within_20_pct"
                ]
                - 5.0
            )
        ),
    }

    return {
        "passed": all(
            checks.values()
        ),
        "comparison_available": True,
        "checks": checks,
        "current_model_metrics":
            current_model,
        "candidate_model_metrics":
            candidate,
    }


def get_feature_importance(
    fitted_model,
) -> pd.DataFrame:
    regressor_pipeline = (
        fitted_model.regressor_
    )

    preprocessor = (
        regressor_pipeline
        .named_steps["preprocessor"]
    )

    try:
        feature_names = (
            preprocessor
            .get_feature_names_out()
        )
    except Exception:
        feature_names = np.array(
            [
                f"feature_{index}"
                for index in range(
                    len(
                        regressor_pipeline
                        .named_steps[
                            "model"
                        ]
                        .feature_importances_
                    )
                )
            ]
        )

    importances = (
        regressor_pipeline
        .named_steps["model"]
        .feature_importances_
    )

    if (
        len(feature_names)
        != len(importances)
    ):
        raise RuntimeError(
            "Feature-name and importance "
            "lengths do not match."
        )

    return (
        pd.DataFrame({
            "feature":
                feature_names,
            "importance":
                importances,
        })
        .sort_values(
            "importance",
            ascending=False,
        )
        .reset_index(drop=True)
    )


def save_json(
    path: Path,
    value: Any,
) -> None:
    path.write_text(
        json.dumps(
            value,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def main() -> None:
    print(
        "Loading non-Realtor training data..."
    )
    raw = fetch_training_data()

    print(
        "Raw rows:",
        f"{len(raw):,}",
    )

    clean = clean_training_data(
        raw
    )

    print(
        "Rows after target cleaning:",
        f"{len(clean):,}",
    )
    print(
        "Features:",
        len(FEATURE_COLUMNS),
    )

    X = clean[
        FEATURE_COLUMNS
    ].copy()

    y = (
        clean[TARGET_COLUMN]
        .astype(float)
        .copy()
    )

    (
        X_train,
        X_test,
        y_train,
        y_test,
    ) = train_test_split(
        X,
        y,
        test_size=TEST_SIZE,
        random_state=RANDOM_STATE,
    )

    print(
        "Training rows:",
        f"{len(X_train):,}",
    )
    print(
        "Testing rows:",
        f"{len(X_test):,}",
    )

    baseline = DummyRegressor(
        strategy="median"
    )
    model = build_model()

    print(
        "Training median baseline..."
    )
    baseline.fit(
        X_train,
        y_train,
    )

    print(
        "Training non-Realtor "
        "Random Forest model..."
    )
    model.fit(
        X_train,
        y_train,
    )

    print(
        "Evaluating candidate model..."
    )
    baseline_predictions = (
        baseline.predict(X_test)
    )
    model_predictions = (
        model.predict(X_test)
    )

    baseline_metrics = (
        evaluate_predictions(
            y_test,
            baseline_predictions,
            "median_baseline",
        )
    )

    candidate_metrics = (
        evaluate_predictions(
            y_test,
            model_predictions,
            (
                "random_forest_proxy_model_"
                "without_listing_data"
            ),
        )
    )

    current_metrics = (
        load_current_metrics()
    )

    validation = (
        validate_candidate_metrics(
            candidate_metrics,
            current_metrics,
        )
    )

    print()
    print("Candidate metrics:")
    print(
        json.dumps(
            candidate_metrics,
            indent=2,
        )
    )

    if validation[
        "comparison_available"
    ]:
        print()
        print(
            "Performance guardrails:"
        )

        for name, passed in (
            validation["checks"].items()
        ):
            print(
                f"  {name}: "
                f"{'PASS' if passed else 'FAIL'}"
            )

    prediction_examples = (
        X_test.copy()
    )
    prediction_examples[
        "actual_proxy_value"
    ] = y_test.to_numpy()

    prediction_examples[
        "predicted_proxy_value"
    ] = model_predictions

    prediction_examples[
        "absolute_error"
    ] = np.abs(
        prediction_examples[
            "actual_proxy_value"
        ]
        - prediction_examples[
            "predicted_proxy_value"
        ]
    )

    prediction_examples[
        "percent_error"
    ] = np.where(
        prediction_examples[
            "actual_proxy_value"
        ]
        > 0,
        (
            prediction_examples[
                "absolute_error"
            ]
            / prediction_examples[
                "actual_proxy_value"
            ]
            * 100
        ),
        np.nan,
    )

    segment_rows = []

    segment_frame = pd.DataFrame({
        "model_segment":
            X_test[
                "model_segment"
            ].to_numpy(),
        "actual_proxy_value":
            y_test.to_numpy(),
        "predicted_proxy_value":
            model_predictions,
    })

    for segment, group in (
        segment_frame.groupby(
            "model_segment"
        )
    ):
        segment_rows.append(
            evaluate_predictions(
                group[
                    "actual_proxy_value"
                ],
                group[
                    "predicted_proxy_value"
                ],
                f"segment_{segment}",
            )
        )

    segment_metrics = (
        pd.DataFrame(
            segment_rows
        )
        .sort_values(
            "row_count",
            ascending=False,
        )
    )

    feature_importance = (
        get_feature_importance(model)
    )

    metadata = {
        "model_name": (
            "yellowstone_proxy_value_"
            "random_forest_v2_no_listing_data"
        ),
        "model_type": (
            "TransformedTargetRegressor"
            "(RandomForestRegressor)"
        ),
        "created_at": utc_now(),
        "target_column":
            TARGET_COLUMN,
        "target_description": (
            "Public parcel total_value "
            "proxy, not closed sale price."
        ),
        "training_table_or_view":
            TRAINING_VIEW,
        "row_count_raw":
            int(len(raw)),
        "row_count_after_cleaning":
            int(len(clean)),
        "train_rows":
            int(len(X_train)),
        "test_rows":
            int(len(X_test)),
        "test_size":
            TEST_SIZE,
        "random_state":
            RANDOM_STATE,
        "max_target_value":
            MAX_TARGET_VALUE,
        "include_assessed_components":
            False,
        "listing_portal_data_included":
            False,
        "excluded_data_sources": [
            "Realtor.com",
            "Zillow",
            "MLS",
        ],
        "numeric_features":
            NUMERIC_FEATURES,
        "categorical_features":
            CATEGORICAL_FEATURES,
        "feature_columns":
            FEATURE_COLUMNS,
        "explicitly_removed_features":
            REALTOR_FEATURES,
        "disclaimer": (
            "Decision-support proxy model "
            "based on public parcel and "
            "government economic data. "
            "Not an appraisal, MLS valuation, "
            "CMA, or guaranteed sale-price "
            "estimate."
        ),
    }

    print(
        "Saving candidate artifacts..."
    )

    model_path = (
        OUTPUT_DIR
        / "price_model_compressed.joblib"
    )

    joblib.dump(
        model,
        model_path,
        compress=3,
    )

    save_json(
        OUTPUT_DIR
        / "feature_columns.json",
        FEATURE_COLUMNS,
    )

    save_json(
        OUTPUT_DIR
        / "model_metrics.json",
        {
            "baseline_metrics":
                baseline_metrics,
            "model_metrics":
                candidate_metrics,
        },
    )

    save_json(
        OUTPUT_DIR
        / "model_metadata.json",
        metadata,
    )

    save_json(
        OUTPUT_DIR
        / "validation_report.json",
        validation,
    )

    feature_importance.to_csv(
        OUTPUT_DIR
        / "training_feature_importance.csv",
        index=False,
    )

    segment_metrics.to_csv(
        OUTPUT_DIR
        / "segment_model_metrics.csv",
        index=False,
    )

    (
        prediction_examples
        .sort_values(
            "percent_error"
        )
        .head(500)
        .to_csv(
            OUTPUT_DIR
            / (
                "model_training_"
                "sample_predictions.csv"
            ),
            index=False,
        )
    )

    reloaded_model = joblib.load(
        model_path
    )

    verification_frame = (
        X_test[
            FEATURE_COLUMNS
        ]
        .head(10)
        .copy()
    )

    original_predictions = (
        model.predict(
            verification_frame
        )
    )

    reloaded_predictions = (
        reloaded_model.predict(
            verification_frame
        )
    )

    if not np.allclose(
        original_predictions,
        reloaded_predictions,
    ):
        raise RuntimeError(
            "Reloaded candidate model "
            "predictions do not match."
        )

    print()
    print(
        "Candidate artifact verification "
        "passed."
    )
    print(
        "Output directory:",
        OUTPUT_DIR,
    )
    print(
        "Model file:",
        model_path,
    )
    model_size_mb = (
        model_path.stat().st_size
        / (1024 * 1024)
    )

    print(
        "Model size:",
        f"{model_size_mb:.2f} MB",
    )

    if not validation["passed"]:
        raise RuntimeError(
            "Candidate model failed one or "
            "more performance guardrails. "
            "The current production model "
            "was not changed."
        )

    print()
    print(
        "Candidate model passed all "
        "performance guardrails."
    )
    print(
        "The production model has not "
        "been overwritten."
    )


if __name__ == "__main__":
    main()