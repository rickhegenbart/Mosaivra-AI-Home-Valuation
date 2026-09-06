from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.supabase_client import get_supabase


JOB_NAME = "update_fema_disaster_declarations"
SOURCE_NAME = "OpenFEMA Disaster Declarations Summaries"
SOURCE_URL = (
    "https://www.fema.gov/openfema-data-page/"
    "disaster-declarations-summaries-v2"
)
API_URL = (
    "https://www.fema.gov/api/open/v2/"
    "DisasterDeclarationsSummaries"
)

SUMMARY_TABLE = "fema_disaster_declaration_summary"
PIPELINE_RUN_TABLE = "data_pipeline_runs"

STATE_ABBREVIATION = "MT"
COUNTY_FIPS = "30111"
FEMA_COUNTY_FIPS = "111"
GEOGRAPHY_NAME = "Yellowstone County, MT"

PAGE_SIZE = 1000
REQUEST_TIMEOUT_SECONDS = 90
REQUEST_RETRIES = 4

KNOWN_INCIDENT_TYPES = {
    "Biological": "biological_declaration_count",
    "Fire": "fire_declaration_count",
    "Flood": "flood_declaration_count",
    "Hurricane": "hurricane_declaration_count",
    "Severe Storm": "severe_storm_declaration_count",
}


def utc_now() -> str:
    return datetime.now(
        timezone.utc
    ).isoformat()


def request_json(
    url: str,
) -> Dict[str, Any]:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Parcel-Proxy-AI/1.0 "
                "(public-data pipeline)"
            ),
            "Accept": "application/json",
        },
    )

    last_error: Optional[Exception] = None

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
                    "OpenFEMA returned an unexpected "
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
                "  OpenFEMA request failed; "
                f"retrying in {wait_seconds} seconds..."
            )
            time.sleep(wait_seconds)

    raise RuntimeError(
        "OpenFEMA request failed after "
        f"{REQUEST_RETRIES} attempts: "
        f"{last_error}"
    )


def build_api_url(
    offset: int,
) -> str:
    query = urllib.parse.urlencode({
        "$filter": (
            "state eq 'MT' and "
            "fipsCountyCode eq '111'"
        ),
        "$orderby": "declarationDate asc",
        "$top": PAGE_SIZE,
        "$skip": offset,
    })

    return f"{API_URL}?{query}"


def fetch_declarations() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    offset = 0

    while True:
        payload = request_json(
            build_api_url(offset)
        )

        page = payload.get(
            "DisasterDeclarationsSummaries",
            [],
        )

        if not isinstance(page, list):
            raise RuntimeError(
                "OpenFEMA declaration collection "
                "was not a list."
            )

        rows.extend(page)

        if len(page) < PAGE_SIZE:
            break

        offset += PAGE_SIZE

    if not rows:
        raise RuntimeError(
            "OpenFEMA returned no Yellowstone "
            "County disaster declarations."
        )

    return rows


def normalize_text(
    value: Any,
) -> Optional[str]:
    if value is None:
        return None

    text = str(value).strip()

    return text or None


def normalize_county_fips(
    value: Any,
) -> str:
    text = normalize_text(value) or ""
    digits = "".join(
        character
        for character in text
        if character.isdigit()
    )

    return digits.zfill(3)[-3:]


def normalize_disaster_number(
    value: Any,
) -> int:
    try:
        disaster_number = int(value)
    except (
        TypeError,
        ValueError,
    ) as error:
        raise RuntimeError(
            "OpenFEMA contains an invalid "
            f"disaster number: {value}"
        ) from error

    if disaster_number <= 0:
        raise RuntimeError(
            "OpenFEMA contains a non-positive "
            f"disaster number: {value}"
        )

    return disaster_number


def validate_source_rows(
    rows: List[Dict[str, Any]],
) -> None:
    if not rows:
        raise RuntimeError(
            "No disaster declaration rows "
            "were provided for validation."
        )

    invalid_rows = []

    for row in rows:
        state = normalize_text(
            row.get("state")
        )
        county_fips = normalize_county_fips(
            row.get("fipsCountyCode")
        )

        if (
            state != STATE_ABBREVIATION
            or county_fips
            != FEMA_COUNTY_FIPS
        ):
            invalid_rows.append({
                "disasterNumber":
                    row.get("disasterNumber"),
                "state": state,
                "fipsCountyCode":
                    county_fips,
            })

    if invalid_rows:
        raise RuntimeError(
            "OpenFEMA returned rows outside "
            "Yellowstone County, Montana: "
            f"{invalid_rows[:10]}"
        )


def deduplicate_declarations(
    rows: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    by_disaster_number: Dict[
        int,
        Dict[str, Any],
    ] = {}

    for row in rows:
        disaster_number = (
            normalize_disaster_number(
                row.get("disasterNumber")
            )
        )

        current = (
            by_disaster_number.get(
                disaster_number
            )
        )

        if current is None:
            by_disaster_number[
                disaster_number
            ] = row
            continue

        current_refresh = (
            normalize_text(
                current.get("lastRefresh")
            )
            or ""
        )
        candidate_refresh = (
            normalize_text(
                row.get("lastRefresh")
            )
            or ""
        )

        if (
            candidate_refresh
            > current_refresh
        ):
            by_disaster_number[
                disaster_number
            ] = row

    return sorted(
        by_disaster_number.values(),
        key=lambda row: (
            normalize_text(
                row.get("declarationDate")
            )
            or "",
            normalize_disaster_number(
                row.get("disasterNumber")
            ),
        ),
    )


def build_summary_row(
    declarations: List[
        Dict[str, Any]
    ],
) -> Dict[str, Any]:
    if not declarations:
        raise RuntimeError(
            "No unique declarations were "
            "available for summarization."
        )

    incident_counts = Counter(
        normalize_text(
            row.get("incidentType")
        )
        or "Unknown"
        for row in declarations
    )

    latest = max(
        declarations,
        key=lambda row: (
            normalize_text(
                row.get("declarationDate")
            )
            or ""
        ),
    )

    earliest = min(
        declarations,
        key=lambda row: (
            normalize_text(
                row.get("declarationDate")
            )
            or ""
        ),
    )

    known_total = sum(
        incident_counts.get(
            incident_type,
            0,
        )
        for incident_type
        in KNOWN_INCIDENT_TYPES
    )

    total_count = len(declarations)
    other_count = (
        total_count - known_total
    )

    latest_refresh_values = [
        normalize_text(
            row.get("lastRefresh")
        )
        for row in declarations
        if normalize_text(
            row.get("lastRefresh")
        )
    ]

    latest_source_refresh = (
        max(latest_refresh_values)
        if latest_refresh_values
        else None
    )

    latest_declaration_date = (
        normalize_text(
            latest.get("declarationDate")
        )
    )

    latest_year = (
        latest_declaration_date[:4]
        if latest_declaration_date
        else None
    )

    row: Dict[str, Any] = {
        "county_fips": COUNTY_FIPS,
        "state_abbreviation":
            STATE_ABBREVIATION,
        "geography_name":
            GEOGRAPHY_NAME,
        "geography_level": "county",
        "total_declaration_count":
            total_count,
        "biological_declaration_count":
            incident_counts.get(
                "Biological",
                0,
            ),
        "fire_declaration_count":
            incident_counts.get(
                "Fire",
                0,
            ),
        "flood_declaration_count":
            incident_counts.get(
                "Flood",
                0,
            ),
        "hurricane_declaration_count":
            incident_counts.get(
                "Hurricane",
                0,
            ),
        "severe_storm_declaration_count":
            incident_counts.get(
                "Severe Storm",
                0,
            ),
        "other_declaration_count":
            other_count,
        "earliest_declaration_date":
            normalize_text(
                earliest.get(
                    "declarationDate"
                )
            ),
        "latest_declaration_date":
            latest_declaration_date,
        "latest_disaster_number":
            normalize_disaster_number(
                latest.get(
                    "disasterNumber"
                )
            ),
        "latest_declaration_type":
            normalize_text(
                latest.get(
                    "declarationType"
                )
            ),
        "latest_incident_type":
            normalize_text(
                latest.get(
                    "incidentType"
                )
            ),
        "latest_declaration_title":
            normalize_text(
                latest.get(
                    "declarationTitle"
                )
            ),
        "latest_designated_area":
            normalize_text(
                latest.get(
                    "designatedArea"
                )
            ),
        "latest_source_refresh":
            latest_source_refresh,
        "source_name": SOURCE_NAME,
        "source_url": SOURCE_URL,
        "source_period":
            "all available records",
        "source_date":
            latest_declaration_date,
        "latest_declaration_year":
            int(latest_year)
            if latest_year
            else None,
        "confidence_level":
            "context_only",
        "notes": (
            "County-level federal disaster "
            "declaration history from OpenFEMA. "
            "Declarations describe designated "
            "public emergencies and do not "
            "measure parcel-specific exposure, "
            "damage, future risk, neighborhood "
            "desirability, political opinion, "
            "or property value."
        ),
        "updated_at": utc_now(),
    }

    return row


def validate_summary(
    row: Dict[str, Any],
    declarations: List[
        Dict[str, Any]
    ],
) -> None:
    total_count = int(
        row["total_declaration_count"]
    )

    component_total = sum([
        int(
            row[
                "biological_declaration_count"
            ]
        ),
        int(
            row[
                "fire_declaration_count"
            ]
        ),
        int(
            row[
                "flood_declaration_count"
            ]
        ),
        int(
            row[
                "hurricane_declaration_count"
            ]
        ),
        int(
            row[
                "severe_storm_declaration_count"
            ]
        ),
        int(
            row[
                "other_declaration_count"
            ]
        ),
    ])

    if total_count != len(declarations):
        raise RuntimeError(
            "Summary declaration count does "
            "not match the deduplicated source."
        )

    if total_count != component_total:
        raise RuntimeError(
            "Declaration total does not equal "
            "its incident-type components: "
            f"{total_count} versus "
            f"{component_total}"
        )

    if row["county_fips"] != COUNTY_FIPS:
        raise RuntimeError(
            "Summary contains an unexpected "
            "county FIPS."
        )

    if (
        not row.get(
            "latest_declaration_date"
        )
        or not row.get(
            "latest_disaster_number"
        )
        or not row.get(
            "latest_incident_type"
        )
    ):
        raise RuntimeError(
            "Latest declaration metadata "
            "is incomplete."
        )


def start_pipeline_run(
    client,
) -> Optional[str]:
    values = {
        "job_name": JOB_NAME,
        "source_name": SOURCE_NAME,
        "status": "running",
        "started_at": utc_now(),
        "rows_read": 0,
        "rows_written": 0,
        "validation_summary": {},
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
        "status": status,
        "completed_at": utc_now(),
        "source_period": source_period,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "validation_summary":
            validation_summary or {},
        "error_message": error_message,
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


def upload_summary(
    client,
    row: Dict[str, Any],
) -> None:
    (
        client
        .table(SUMMARY_TABLE)
        .upsert(
            row,
            on_conflict="county_fips",
        )
        .execute()
    )


def main() -> None:
    client = get_supabase()
    run_id = start_pipeline_run(client)

    source_rows: List[
        Dict[str, Any]
    ] = []
    declarations: List[
        Dict[str, Any]
    ] = []
    summary: Optional[
        Dict[str, Any]
    ] = None

    try:
        print(
            "Downloading Yellowstone County "
            "OpenFEMA disaster declarations..."
        )
        source_rows = fetch_declarations()

        print(
            "Validating OpenFEMA source rows..."
        )
        validate_source_rows(source_rows)

        print(
            "Deduplicating FEMA disasters..."
        )
        declarations = (
            deduplicate_declarations(
                source_rows
            )
        )

        print(
            "Preparing disaster declaration "
            "summary..."
        )
        summary = build_summary_row(
            declarations
        )

        print(
            "Validating disaster declaration "
            "summary..."
        )
        validate_summary(
            summary,
            declarations,
        )

        print(
            "Updating FEMA disaster declaration "
            "summary..."
        )
        upload_summary(
            client,
            summary,
        )

        validation_summary = {
            "source_row_count":
                len(source_rows),
            "distinct_disaster_count":
                len(declarations),
            "total_declaration_count":
                summary[
                    "total_declaration_count"
                ],
            "biological_count":
                summary[
                    "biological_declaration_count"
                ],
            "fire_count":
                summary[
                    "fire_declaration_count"
                ],
            "flood_count":
                summary[
                    "flood_declaration_count"
                ],
            "hurricane_count":
                summary[
                    "hurricane_declaration_count"
                ],
            "severe_storm_count":
                summary[
                    "severe_storm_declaration_count"
                ],
            "other_count":
                summary[
                    "other_declaration_count"
                ],
            "latest_disaster_number":
                summary[
                    "latest_disaster_number"
                ],
        }

        finish_pipeline_run(
            client,
            run_id,
            "succeeded",
            source_period=(
                summary[
                    "source_period"
                ]
            ),
            rows_read=len(source_rows),
            rows_written=1,
            validation_summary=(
                validation_summary
            ),
        )

        print()
        print(
            "FEMA disaster declaration "
            "update succeeded."
        )
        print(
            "Source rows:",
            len(source_rows),
        )
        print(
            "Distinct disasters:",
            len(declarations),
        )
        print(
            "Total declarations:",
            summary[
                "total_declaration_count"
            ],
        )
        print(
            "Biological:",
            summary[
                "biological_declaration_count"
            ],
        )
        print(
            "Fire:",
            summary[
                "fire_declaration_count"
            ],
        )
        print(
            "Flood:",
            summary[
                "flood_declaration_count"
            ],
        )
        print(
            "Hurricane:",
            summary[
                "hurricane_declaration_count"
            ],
        )
        print(
            "Severe Storm:",
            summary[
                "severe_storm_declaration_count"
            ],
        )
        print(
            "Other:",
            summary[
                "other_declaration_count"
            ],
        )
        print(
            "Latest declaration:",
            (
                f"DR-{summary['latest_disaster_number']} "
                f"| {summary['latest_incident_type']} "
                f"| {summary['latest_declaration_date'][:10]} "
                f"| {summary['latest_declaration_title']}"
            ),
        )

    except Exception as error:
        source_period = (
            summary.get("source_period")
            if summary
            else None
        )

        finish_pipeline_run(
            client,
            run_id,
            "failed",
            source_period=source_period,
            rows_read=len(source_rows),
            rows_written=0,
            validation_summary={},
            error_message=str(error),
        )

        raise


if __name__ == "__main__":
    main()