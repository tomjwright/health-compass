import gzip
import json
from pathlib import Path
from typing import Any

import duckdb
import pytest

from health_compass import transform

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
                    "city": "Singapore",
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
        "statusModule": {"overallStatus": "RECRUITING"},
    }
}


def save_load(name: str, studies: list[dict[str, Any]], complete: bool = True) -> None:
    run_dir = transform.raw_dir() / name
    run_dir.mkdir(parents=True)
    page = json.dumps({"studies": studies}).encode()
    (run_dir / "page-00001.json.gz").write_bytes(gzip.compress(page))
    manifest = {"data_timestamp": name, "loaded_at": name, "complete": complete}
    (run_dir / "manifest.json").write_text(json.dumps(manifest))


def query(sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(transform.warehouse_path()), read_only=True) as db:
        return db.execute(sql).fetchall()


@pytest.fixture(autouse=True)
def dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))


def test_builds_the_trial_tables_from_the_latest_complete_load() -> None:
    save_load("2026-11-15", [BARE_STUDY])
    save_load("2026-11-16", [FULL_STUDY, BARE_STUDY])
    save_load("2026-11-17", [BARE_STUDY], complete=False)  # a --max-pages run: skipped
    transform.build()

    assert query("select nct_id, phases, start_date, enrollment from trials order by 1") == [
        ("NCT00000001", ["PHASE2", "PHASE3"], "2025-04", 120),
        ("NCT00000002", None, None, None),
    ]
    assert query("select condition, source from trial_conditions order by 2") == [
        ("Alopecia", "mesh"),
        ("Hair Loss", "reported"),
    ]
    assert query("select intervention_type, intervention_name from trial_interventions") == [
        ("DRUG", "Minoxidil")
    ]
    assert query("select city, latitude, longitude from trial_sites order by 1") == [
        ("Nowhere", None, None),
        ("Singapore", 1.29, 103.85),
    ]


def test_failed_build_leaves_the_live_database_alone() -> None:
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()

    no_id = {"protocolSection": {"statusModule": {"overallStatus": "RECRUITING"}}}
    save_load("2026-11-17", [FULL_STUDY, no_id])
    with pytest.raises(SystemExit, match="dbt build failed"):
        transform.build()
    assert query("select nct_id from trials") == [("NCT00000001",)]


def test_needs_a_complete_load() -> None:
    save_load("2026-11-17", [FULL_STUDY], complete=False)
    with pytest.raises(SystemExit, match="No complete"):
        transform.build()
