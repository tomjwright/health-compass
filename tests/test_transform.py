import json
from datetime import date
from pathlib import Path
from typing import Any

import dlt
import duckdb
import pytest

from health_compass import transform
from health_compass.ingest import clinicaltrials, common
from health_compass.ingest.common import raw_warehouse_path

FULL_STUDY = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00000001", "briefTitle": "Oral minoxidil"},
        "statusModule": {
            "overallStatus": "RECRUITING",
            "startDateStruct": {"date": "2025-04"},
            "lastUpdatePostDateStruct": {"date": "2026-06-15"},
        },
        "designModule": {"phases": ["PHASE2", "PHASE3"], "enrollmentInfo": {"count": 120}},
        "eligibilityModule": {"minimumAge": "18 Years", "healthyVolunteers": False},
        "conditionsModule": {"conditions": ["Hair Loss"]},
        "armsInterventionsModule": {"interventions": [{"type": "DRUG", "name": "Minoxidil"}]},
        "contactsLocationsModule": {
            "locations": [
                {
                    "facility": "National Skin Centre",
                    "status": "RECRUITING",
                    "city": "Singapore",
                    "state": "Central",
                    "country": "Singapore",
                    "geoPoint": {"lat": 1.29, "lon": 103.85},
                },
                {"city": "Nowhere", "country": "Unknown"},
            ]
        },
    },
    "derivedSection": {
        "conditionBrowseModule": {"meshes": [{"id": "D000505", "term": "Alopecia"}]}
    },
}
BARE_STUDY = {
    "protocolSection": {
        "identificationModule": {"nctId": "NCT00000002"},
        "statusModule": {
            "overallStatus": "RECRUITING",
            "lastUpdatePostDateStruct": {"date": "2026-01-10"},
        },
    }
}


def updated(study: dict[str, Any], status: str, day: str) -> dict[str, Any]:
    """A later version of `study`, with a new status, as posted on `day`."""
    copy = json.loads(json.dumps(study))
    copy["protocolSection"]["statusModule"]["overallStatus"] = status
    copy["protocolSection"]["statusModule"]["lastUpdatePostDateStruct"] = {"date": day}
    return copy


def save_load(data_timestamp: str, studies: list[dict[str, Any]]) -> None:
    clinicaltrials.load_studies(studies, data_timestamp)


def query(sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(transform.warehouse_path()), read_only=True) as db:
        return db.execute(sql).fetchall()


@pytest.fixture(autouse=True)
def dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))
    save_approvals()


def test_builds_the_star_schema_from_the_loads() -> None:
    save_load("2026-11-15", [BARE_STUDY])
    save_load("2026-11-16", [FULL_STUDY, BARE_STUDY])
    transform.build()

    assert query(
        "select nct_id, phases, start_date, is_recruiting, enrollment"
        " from marts.fct_trials join marts.dim_trials using (trial_key) order by 1"
    ) == [
        ("NCT00000001", ["PHASE2", "PHASE3"], "2025-04", True, 120),
        ("NCT00000002", None, None, True, None),
    ]
    assert query(
        "select condition_name, vocabulary, mesh_id from marts.bridge_trial_conditions"
        " join marts.dim_conditions using (condition_key) order by 2"
    ) == [("Alopecia", "mesh", "D000505"), ("Hair Loss", "reported", None)]
    assert query(
        "select intervention_type, intervention_name from marts.bridge_trial_interventions"
        " join marts.dim_interventions using (intervention_key)"
    ) == [("DRUG", "Minoxidil")]
    assert query(
        "select city, latitude, longitude from marts.fct_trial_sites"
        " join marts.dim_locations using (location_key) order by 1"
    ) == [("Nowhere", None, None), ("Singapore", 1.29, 103.85)]


def test_keeps_each_version_of_a_trial_and_its_status_changes() -> None:
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()
    # Its first load is changed in raw.duckdb too, to show the build reads only the new load.
    with duckdb.connect(str(raw_warehouse_path())) as raw:
        raw.execute(
            "update clinicaltrials.studies"
            " set protocol_section__identification_module__brief_title = 'Changed'"
        )
    # The next day's update: the trial has stopped recruiting.
    never_recruiting = updated(BARE_STUDY, "COMPLETED", "2026-11-17")
    never_recruiting["protocolSection"]["identificationModule"]["nctId"] = "NCT00000003"
    save_load("2026-11-17", [updated(FULL_STUDY, "COMPLETED", "2026-11-17"), never_recruiting])
    transform.build()

    assert query(
        "select nct_id, valid_from, valid_to, is_current, overall_status, is_recruiting"
        " from marts.dim_trials order by valid_from"
    ) == [
        ("NCT00000001", date(2026, 6, 15), date(2026, 11, 17), False, "RECRUITING", True),
        ("NCT00000001", date(2026, 11, 17), None, True, "COMPLETED", False),
    ]
    assert query(
        "select nct_id, changed_on, from_status, to_status from marts.fct_trial_status_changes"
        " join marts.dim_trials using (trial_key)"
    ) == [("NCT00000001", date(2026, 11, 17), "RECRUITING", "COMPLETED")]
    assert query("select count(*) from marts.fct_trial_sites") == [(2,)]  # current version only

    first_version = "select title from marts.dim_trials where not is_current"
    assert query(first_version) == [("Oral minoxidil",)]
    transform.build(full_refresh=True)  # replays the raw loads, so picks up the change
    assert query(first_version) == [("Changed",)]


def test_failed_build_leaves_the_live_database_alone() -> None:
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()

    no_id = {"protocolSection": {"statusModule": {"overallStatus": "RECRUITING"}}}
    save_load("2026-11-17", [FULL_STUDY, no_id])
    with pytest.raises(SystemExit, match="dbt build failed"):
        transform.build()
    assert query("select nct_id from marts.dim_trials") == [("NCT00000001",)]


def test_needs_a_clinicaltrials_load() -> None:
    with pytest.raises(SystemExit, match=r"No ClinicalTrials\.gov load"):
        transform.build()


def save_approvals() -> None:
    application = {
        "application_number": "NDA018154",
        "sponsor_name": "PFIZER",
        "products": [
            {
                "brand_name": "LONITEN",
                "marketing_status": "Prescription",
                "active_ingredients": [{"name": "MINOXIDIL", "strength": "2.5MG"}],
            },
            {
                "brand_name": "LONITEN PLUS",
                "marketing_status": "Discontinued",
                "active_ingredients": [{"name": "MINOXIDIL"}, {"name": "RESERPINE"}],
            },
        ],
        "submissions": [
            {
                "submission_type": "SUPPL",
                "submission_status": "AP",
                "submission_status_date": "20200101",
            },
            {
                "submission_type": "ORIG",
                "submission_status": "AP",
                "submission_status_date": "19790812",
            },
        ],
    }
    labels = [
        {"set_id": "old", "effective_time": "20190101", "indications_and_usage": ["Old text"]},
        {"set_id": "new", "effective_time": "20251008", "indications_and_usage": ["Hypertension"]},
    ]
    for label in labels:
        label["openfda"] = {"application_number": ["NDA018154"], "brand_name": ["Loniten"]}
    medicine = {
        "ema_product_number": "EMEA/H/C/004297",
        "name_of_medicine": "Talmanco",
        "active_substance": "Tadalafil",
        "medicine_status": "Authorised",
        "marketing_authorisation_date": "09/01/2017",
        "conditional_approval": "No",
        "orphan_medicine": "No",
        "therapeutic_area_mesh": "Hypertension, Pulmonary;Erectile Dysfunction",
        "therapeutic_indication": "Treatment of pulmonary arterial hypertension",
        "marketing_authorisation_developer_applicant_holder": "Viatris Limited",
        "medicine_url": "https://www.ema.europa.eu/en/medicines/human/EPAR/talmanco",
    }
    openfda = [
        dlt.resource([application], name="applications", write_disposition="replace"),
        dlt.resource(labels, name="labels", write_disposition="replace"),
    ]
    common.pipeline("openfda").run(openfda)
    common.pipeline("ema").run(
        dlt.resource([medicine], name="medicines", write_disposition="replace")
    )


def test_builds_the_approvals_from_the_newest_load() -> None:
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()
    save_approvals()
    transform.build()  # the second load replaces the first rather than adding to it

    assert query(
        "select region, approval_id, medicine_name, active_substance, status, approved_on,"
        " indications from marts.fct_approvals order by 1 desc, 3"
    ) == [
        ("US", "NDA018154", "LONITEN", "minoxidil", "Marketed", date(1979, 8, 12), "Hypertension"),
        (
            "US",
            "NDA018154",
            "LONITEN PLUS",
            "minoxidil / reserpine",
            "Discontinued",
            date(1979, 8, 12),
            None,  # its application has two brands and no label names this one
        ),
        (
            "EU",
            "EMEA/H/C/004297",
            "Talmanco",
            "tadalafil",
            "Authorised",
            date(2017, 1, 9),
            "Treatment of pulmonary arterial hypertension",
        ),
    ]
    assert query("select condition_name from marts.bridge_approval_conditions order by 1") == [
        ("Erectile Dysfunction",),
        ("Hypertension, Pulmonary",),
    ]


def test_builds_the_trials_without_approvals_data(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("RAW_DUCKDB_PATH", str(tmp_path / "no-approvals" / "raw.duckdb"))
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()
    assert query("select count(*) from marts.dim_trials") == [(1,)]
    tables = query("select table_name from duckdb_tables() where schema_name = 'marts'")
    assert ("fct_approvals",) not in tables
