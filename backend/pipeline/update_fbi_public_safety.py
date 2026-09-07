from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.supabase_client import get_supabase


JOB_NAME = "update_fbi_public_safety"
SOURCE_NAME = "FBI Crime Data Explorer"
SOURCE_URL = (
    "https://cde.ucr.cjis.gov/LATEST/"
)
API_BASE_URL = (
    "https://api.usa.gov/crime/fbi/cde"
)

SUMMARY_TABLE = "fbi_public_safety_summary"
PIPELINE_RUN_TABLE = "data_pipeline_runs"

STATE_ABBREVIATION = "MT"
COUNTY_FIPS = "30111"

BILLINGS_ORI = "MT0560100"

YELLOWSTONE_AGENCIES = {
    "MT0560000":
        "Yellowstone County Sheriff's Office",
    "MT0560100":
        "Billings Police Department",
    "MT0560200":
        "Laurel Police Department",
    "MT0560600":
        "Montana State University: Billings",
}

OFFENSE_ENDPOINTS = {
    "violent_crime_count":
        "violent-crime",
    "property_crime_count":
        "property-crime",
    "homicide_count":
        "homicide",
    "rape_count":
        "rape",
    "robbery_count":
        "robbery",
    "aggravated_assault_count":
        "aggravated-assault",
    "burglary_count":
        "burglary",
    "larceny_count":
        "larceny",
    "motor_vehicle_theft_count":
        "motor-vehicle-theft",
    "arson_count":
        "arson",
}

EXPECTED_MONTH_COUNT = 12
REQUEST_TIMEOUT_SECONDS = 90
REQUEST_RETRIES = 4


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def get_api_key() -> str:
    api_key = os.getenv(
        "FBI_API_KEY",
        "",
    ).strip()

    if not api_key:
        raise RuntimeError(
            "FBI_API_KEY is required."
        )

    return api_key


def request_json(
    url: str,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent":
                "Mosaivra-AI-Home-Valuation/1.0 "
                "(public-data pipeline)",
            "Accept": "application/json",
        },
    )

    last_error: Optional[
        Exception
    ] = None

    for attempt in range(
        1,
        REQUEST_RETRIES + 1,
    ):
        try:
            with urllib.request.urlopen(
                request,
                timeout=REQUEST_TIMEOUT_SECONDS,
            ) as response:
                payload = json.load(response)

            if not isinstance(payload, dict):
                raise RuntimeError(
                    "FBI API returned an unexpected "
                    "response type."
                )

            return payload

        except (
            urllib.error.HTTPError,
            urllib.error.URLError,
            TimeoutError,
            json.JSONDecodeError,
        ) as error:
            last_error = error

            if (
                isinstance(
                    error,
                    urllib.error.HTTPError,
                )
                and error.code
                not in {
                    429,
                    500,
                    502,
                    503,
                    504,
                }
            ):
                raise

            if attempt >= REQUEST_RETRIES:
                break

            wait_seconds = 2 ** attempt

            print(
                "  FBI request failed; "
                f"retrying in {wait_seconds} seconds..."
            )
            time.sleep(wait_seconds)

    raise RuntimeError(
        "FBI API request failed after "
        f"{REQUEST_RETRIES} attempts: "
        f"{last_error}"
    )


def build_summary_url(
    ori: str,
    offense_slug: str,
    report_year: int,
    api_key: str,
) -> str:
    query = urllib.parse.urlencode({
        "from": f"01-{report_year}",
        "to": f"12-{report_year}",
        "API_KEY": api_key,
    })

    return (
        f"{API_BASE_URL}/summarized/"
        f"agency/{ori}/{offense_slug}"
        f"?{query}"
    )


def select_agency_series(
    series_by_label: Dict[
        str,
        Dict[str, Any],
    ],
    *,
    suffix: Optional[str] = None,
) -> Tuple[str, Dict[str, Any]]:
    candidates = []

    for label, values in (
        series_by_label or {}
    ).items():
        if not isinstance(values, dict):
            continue

        # Exclude only the exact comparison series. A prefix check would
        # incorrectly exclude "Montana State University: Billings".
        excluded_labels = {
            "Montana",
            "United States",
        }

        if suffix:
            excluded_labels.update({
                f"Montana{suffix}",
                f"United States{suffix}",
            })

        if label in excluded_labels:
            continue

        if (
            suffix
            and not label.endswith(suffix)
        ):
            continue

        candidates.append(
            (label, values)
        )

    if len(candidates) != 1:
        raise RuntimeError(
            "Unable to identify a unique "
            "agency series in FBI response. "
            f"Candidates: {[label for label, _ in candidates]}"
        )

    return candidates[0]

def expected_months(
    report_year: int,
) -> List[str]:
    return [
        f"{month:02d}-{report_year}"
        for month in range(1, 13)
    ]


def normalize_monthly_values(
    values: Dict[str, Any],
    report_year: int,
    label: str,
) -> Dict[str, float]:
    months = expected_months(
        report_year
    )

    missing_months = [
        month
        for month in months
        if month not in values
    ]

    if missing_months:
        raise RuntimeError(
            f"{label} is missing months: "
            f"{missing_months}"
        )

    normalized: Dict[
        str,
        float,
    ] = {}

    for month in months:
        value = values.get(month)

        if value is None:
            raise RuntimeError(
                f"{label} has a null value "
                f"for {month}."
            )

        try:
            number = float(value)
        except (
            TypeError,
            ValueError,
        ) as error:
            raise RuntimeError(
                f"{label} contains a "
                f"non-numeric value for "
                f"{month}: {value}"
            ) from error

        if number < 0:
            raise RuntimeError(
                f"{label} contains a "
                f"negative value for "
                f"{month}: {number}"
            )

        normalized[month] = number

    return normalized


def fetch_agency_offense(
    ori: str,
    offense_slug: str,
    report_year: int,
    api_key: str,
) -> Dict[str, Any]:
    url = build_summary_url(
        ori,
        offense_slug,
        report_year,
        api_key,
    )

    payload = request_json(url)

    actuals = (
        payload
        .get("offenses", {})
        .get("actuals", {})
    )

    offense_label, offense_values = (
        select_agency_series(
            actuals,
            suffix=" Offenses",
        )
    )

    monthly_offenses = (
        normalize_monthly_values(
            offense_values,
            report_year,
            offense_label,
        )
    )

    population_series = (
        payload
        .get("populations", {})
        .get("population", {})
    )

    population_label, population_values = (
        select_agency_series(
            population_series
        )
    )

    monthly_population = (
        normalize_monthly_values(
            population_values,
            report_year,
            population_label,
        )
    )

    unique_populations = {
        int(round(value))
        for value
        in monthly_population.values()
    }

    if len(unique_populations) != 1:
        raise RuntimeError(
            f"Population changed within "
            f"{report_year} for {ori}: "
            f"{sorted(unique_populations)}"
        )

    population = next(
        iter(unique_populations)
    )

    annual_count = int(round(sum(
        monthly_offenses.values()
    )))

    return {
        "ori": ori,
        "offense_slug":
            offense_slug,
        "annual_count":
            annual_count,
        "population":
            population,
        "month_count":
            len(monthly_offenses),
    }


def release_is_complete(
    report_year: int,
    api_key: str,
) -> bool:
    try:
        result = fetch_agency_offense(
            BILLINGS_ORI,
            "violent-crime",
            report_year,
            api_key,
        )

        return (
            result["month_count"]
            == EXPECTED_MONTH_COUNT
            and result["population"] > 0
        )

    except Exception as error:
        print(
            f"  {report_year} is not "
            f"complete: {error}"
        )
        return False


def discover_latest_complete_year(
    api_key: str,
) -> int:
    current_year = (
        datetime.now(
            timezone.utc
        ).year
    )

    # Annual FBI releases normally trail
    # the calendar year they describe.
    for report_year in range(
        current_year - 1,
        current_year - 7,
        -1,
    ):
        print(
            "Checking FBI reporting year "
            f"{report_year}..."
        )

        if release_is_complete(
            report_year,
            api_key,
        ):
            return report_year

    raise RuntimeError(
        "No complete FBI annual agency "
        "release was found."
    )


def fetch_agency_metrics(
    ori: str,
    report_year: int,
    api_key: str,
) -> Dict[str, Any]:
    metrics: Dict[str, Any] = {
        "ori": ori,
    }

    populations = set()

    for index, (
        field_name,
        offense_slug,
    ) in enumerate(
        OFFENSE_ENDPOINTS.items(),
        start=1,
    ):
        result = fetch_agency_offense(
            ori,
            offense_slug,
            report_year,
            api_key,
        )

        metrics[field_name] = (
            result["annual_count"]
        )
        populations.add(
            result["population"]
        )

        print(
            f"    {index}/{len(OFFENSE_ENDPOINTS)} "
            f"{offense_slug}: "
            f"{result['annual_count']:,}"
        )

    if len(populations) != 1:
        raise RuntimeError(
            "FBI endpoints returned "
            f"inconsistent populations for "
            f"{ori}: {sorted(populations)}"
        )

    metrics["covered_population"] = (
        next(iter(populations))
    )

    return metrics


def sum_metrics(
    agency_metrics: List[
        Dict[str, Any]
    ],
) -> Dict[str, int]:
    result = {
        "covered_population": sum(
            int(
                row[
                    "covered_population"
                ]
            )
            for row in agency_metrics
        )
    }

    for field_name in (
        OFFENSE_ENDPOINTS
    ):
        result[field_name] = sum(
            int(row[field_name])
            for row in agency_metrics
        )

    return result


def rate_per_1000(
    count: int,
    population: int,
) -> float:
    if population <= 0:
        raise RuntimeError(
            "Covered population must be "
            "greater than zero."
        )

    return round(
        count / population * 1000,
        4,
    )


def build_summary_row(
    *,
    scope_key: str,
    geography_name: str,
    geography_level: str,
    ori_codes: List[str],
    metrics: Dict[str, int],
    report_year: int,
) -> Dict[str, Any]:
    population = int(
        metrics["covered_population"]
    )

    violent_count = int(
        metrics["violent_crime_count"]
    )
    property_count = int(
        metrics["property_crime_count"]
    )

    row: Dict[str, Any] = {
        "scope_key": scope_key,
        "report_year": report_year,
        "geography_name":
            geography_name,
        "geography_level":
            geography_level,
        "state_abbreviation":
            STATE_ABBREVIATION,
        "county_fips":
            COUNTY_FIPS,
        "ori_codes":
            sorted(ori_codes),
        "covered_population":
            population,
        "violent_crime_count":
            violent_count,
        "violent_crime_rate_per_1000":
            rate_per_1000(
                violent_count,
                population,
            ),
        "property_crime_count":
            property_count,
        "property_crime_rate_per_1000":
            rate_per_1000(
                property_count,
                population,
            ),
        "source_name":
            SOURCE_NAME,
        "source_url":
            SOURCE_URL,
        "source_period":
            str(report_year),
        "source_date":
            None,
        "confidence_level":
            "context_only",
        "notes": (
            "Annual summarized reported-offense "
            "context from the FBI Crime Data "
            "Explorer API. Counts describe "
            "offenses reported by participating "
            "law-enforcement agencies. They are "
            "not estimates of unreported crime, "
            "parcel-level or neighborhood-level "
            "safety scores, rankings, steering "
            "guidance, or valuation adjustments."
        ),
        "updated_at": utc_now(),
    }

    for field_name in (
        OFFENSE_ENDPOINTS
    ):
        row[field_name] = int(
            metrics[field_name]
        )

    return row


def build_rows(
    agency_metrics_by_ori: Dict[
        str,
        Dict[str, Any],
    ],
    report_year: int,
) -> List[Dict[str, Any]]:
    billings_metrics = (
        agency_metrics_by_ori[
            BILLINGS_ORI
        ]
    )

    yellowstone_metrics = (
        sum_metrics(list(
            agency_metrics_by_ori.values()
        ))
    )

    return [
        build_summary_row(
            scope_key=
                "billings_police",
            geography_name=
                "Billings Police Department, MT",
            geography_level=
                "city_agency",
            ori_codes=[
                BILLINGS_ORI
            ],
            metrics=
                billings_metrics,
            report_year=
                report_year,
        ),
        build_summary_row(
            scope_key=(
                "yellowstone_"
                "participating_agencies"
            ),
            geography_name=(
                "Yellowstone County, MT "
                "participating agencies"
            ),
            geography_level=(
                "county_agency_group"
            ),
            ori_codes=sorted(
                YELLOWSTONE_AGENCIES
            ),
            metrics=
                yellowstone_metrics,
            report_year=
                report_year,
        ),
    ]


def validate_rows(
    rows: List[Dict[str, Any]],
    report_year: int,
) -> Dict[str, Any]:
    if len(rows) != 2:
        raise RuntimeError(
            "Expected exactly two FBI "
            f"summary rows; found {len(rows)}."
        )

    expected_scopes = {
        "billings_police",
        (
            "yellowstone_"
            "participating_agencies"
        ),
    }

    actual_scopes = {
        row["scope_key"]
        for row in rows
    }

    if actual_scopes != expected_scopes:
        raise RuntimeError(
            "Unexpected FBI summary scopes: "
            f"{sorted(actual_scopes)}"
        )

    for row in rows:
        if row["report_year"] != report_year:
            raise RuntimeError(
                "Prepared FBI row contains "
                "an unexpected report year."
            )

        if (
            row["covered_population"]
            <= 0
        ):
            raise RuntimeError(
                "Prepared FBI row contains "
                "an invalid population."
            )

        for field_name in (
            OFFENSE_ENDPOINTS
        ):
            if row[field_name] < 0:
                raise RuntimeError(
                    f"{field_name} cannot "
                    "be negative."
                )

        expected_violent = (
            row["homicide_count"]
            + row["rape_count"]
            + row["robbery_count"]
            + row[
                "aggravated_assault_count"
            ]
        )

        if (
            row["violent_crime_count"]
            != expected_violent
        ):
            raise RuntimeError(
                "Violent-crime total does "
                "not equal its components "
                f"for {row['scope_key']}: "
                f"{row['violent_crime_count']} "
                f"versus {expected_violent}"
            )

        expected_property = (
            row["burglary_count"]
            + row["larceny_count"]
            + row[
                "motor_vehicle_theft_count"
            ]
            + row["arson_count"]
        )

        if (
            row["property_crime_count"]
            != expected_property
        ):
            raise RuntimeError(
                "Property-crime total does "
                "not equal its components "
                f"for {row['scope_key']}: "
                f"{row['property_crime_count']} "
                f"versus {expected_property}"
            )

    billings = next(
        row
        for row in rows
        if row["scope_key"]
        == "billings_police"
    )

    county = next(
        row
        for row in rows
        if row["scope_key"]
        == (
            "yellowstone_"
            "participating_agencies"
        )
    )

    if (
        county["covered_population"]
        < billings[
            "covered_population"
        ]
    ):
        raise RuntimeError(
            "County participating population "
            "is smaller than Billings."
        )

    return {
        "report_year":
            report_year,
        "scope_count":
            len(rows),
        "agency_count":
            len(
                YELLOWSTONE_AGENCIES
            ),
        "billings_population":
            billings[
                "covered_population"
            ],
        "yellowstone_population":
            county[
                "covered_population"
            ],
        "billings_violent_count":
            billings[
                "violent_crime_count"
            ],
        "yellowstone_violent_count":
            county[
                "violent_crime_count"
            ],
        "billings_property_count":
            billings[
                "property_crime_count"
            ],
        "yellowstone_property_count":
            county[
                "property_crime_count"
            ],
    }


def upload_rows(
    client,
    rows: List[Dict[str, Any]],
) -> None:
    (
        client
        .table(SUMMARY_TABLE)
        .upsert(
            rows,
            on_conflict=(
                "scope_key,report_year"
            ),
        )
        .execute()
    )


def start_pipeline_run(
    client,
) -> Optional[str]:
    values = {
        "source_name":
            SOURCE_NAME,
        "job_name":
            JOB_NAME,
        "status":
            "running",
        "started_at":
            utc_now(),
        "rows_read":
            0,
        "rows_written":
            0,
        "validation_summary":
            {},
    }

    try:
        result = (
            client
            .table(PIPELINE_RUN_TABLE)
            .insert(values)
            .execute()
        )

        data = result.data or []

        if data:
            return data[0].get("id")

    except Exception as error:
        print(
            "Warning: could not create "
            "pipeline run record:",
            error,
        )

    return None


def finish_pipeline_run(
    client,
    run_id: Optional[str],
    status: str,
    *,
    source_period: Optional[str] = None,
    rows_read: int = 0,
    rows_written: int = 0,
    validation_summary: Optional[
        Dict[str, Any]
    ] = None,
    error_message: Optional[str] = None,
) -> None:
    if not run_id:
        return

    values = {
        "status":
            status,
        "completed_at":
            utc_now(),
        "source_period":
            source_period,
        "rows_read":
            rows_read,
        "rows_written":
            rows_written,
        "validation_summary":
            validation_summary or {},
        "error_message":
            error_message,
    }

    try:
        (
            client
            .table(PIPELINE_RUN_TABLE)
            .update(values)
            .eq("id", run_id)
            .execute()
        )

    except Exception as error:
        print(
            "Warning: could not finish "
            "pipeline run record:",
            error,
        )


def main() -> None:
    client = get_supabase()
    run_id = start_pipeline_run(client)

    report_year: Optional[int] = None
    rows_read = 0
    rows_written = 0
    validation_summary: Dict[
        str,
        Any,
    ] = {}

    try:
        api_key = get_api_key()

        print(
            "Discovering latest complete "
            "FBI reporting year..."
        )
        report_year = (
            discover_latest_complete_year(
                api_key
            )
        )

        print(
            "Latest complete FBI year: "
            f"{report_year}"
        )

        agency_metrics_by_ori: Dict[
            str,
            Dict[str, Any],
        ] = {}

        for agency_number, (
            ori,
            agency_name,
        ) in enumerate(
            YELLOWSTONE_AGENCIES.items(),
            start=1,
        ):
            print(
                f"Loading agency "
                f"{agency_number}/"
                f"{len(YELLOWSTONE_AGENCIES)}: "
                f"{agency_name} ({ori})"
            )

            agency_metrics_by_ori[
                ori
            ] = fetch_agency_metrics(
                ori,
                report_year,
                api_key,
            )

            rows_read += (
                len(OFFENSE_ENDPOINTS)
                * EXPECTED_MONTH_COUNT
            )

        print(
            "Preparing FBI public-safety "
            "summaries..."
        )
        rows = build_rows(
            agency_metrics_by_ori,
            report_year,
        )

        print(
            "Validating FBI public-safety "
            "summaries..."
        )
        validation_summary = (
            validate_rows(
                rows,
                report_year,
            )
        )

        print(
            "Updating FBI public-safety "
            "summaries..."
        )
        upload_rows(
            client,
            rows,
        )
        rows_written = len(rows)

        finish_pipeline_run(
            client,
            run_id,
            "succeeded",
            source_period=
                str(report_year),
            rows_read=
                rows_read,
            rows_written=
                rows_written,
            validation_summary=
                validation_summary,
        )

        print()
        print(
            "FBI public-safety update "
            "succeeded."
        )
        print(
            f"Reporting year: "
            f"{report_year}"
        )
        print(
            "Agencies processed: "
            f"{len(agency_metrics_by_ori)}"
        )
        print(
            "Summary rows written: "
            f"{rows_written}"
        )

        for row in rows:
            print(
                f"{row['geography_name']} | "
                f"population "
                f"{row['covered_population']:,} | "
                f"violent "
                f"{row['violent_crime_count']:,} "
                f"({row['violent_crime_rate_per_1000']} "
                "per 1,000) | "
                f"property "
                f"{row['property_crime_count']:,} "
                f"({row['property_crime_rate_per_1000']} "
                "per 1,000)"
            )

    except Exception as error:
        finish_pipeline_run(
            client,
            run_id,
            "failed",
            source_period=(
                str(report_year)
                if report_year
                else None
            ),
            rows_read=
                rows_read,
            rows_written=
                rows_written,
            validation_summary=
                validation_summary,
            error_message=
                str(error),
        )
        raise


if __name__ == "__main__":
    main()
