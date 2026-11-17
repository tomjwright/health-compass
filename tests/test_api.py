from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from test_transform import BARE_STUDY, FULL_STUDY, save_load, updated

from health_compass import transform
from health_compass.api import app

SINGAPORE = {"latitude": 1.29, "longitude": 103.85}


@pytest.fixture(autouse=True)
def dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))


def site(city: str, lat: float, lon: float) -> dict[str, object]:
    return {"city": city, "country": "X", "geoPoint": {"lat": lat, "lon": lon}}


def trial(nct_id: str, *sites: dict[str, object]) -> dict[str, object]:
    study = updated(FULL_STUDY, "RECRUITING", "2026-06-15")
    study["protocolSection"]["identificationModule"]["nctId"] = nct_id
    study["protocolSection"]["contactsLocationsModule"]["locations"] = list(sites)
    return study


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_finds_trials_nearest_first_with_stopped_ones_last() -> None:
    near = trial("NCT00000011", site("Kuala Lumpur", 3.14, 101.69), site("Singapore", 1.29, 103.85))
    far = trial("NCT00000012", site("London", 51.51, -0.13))
    stopped = trial("NCT00000013", site("Johor Bahru", 1.49, 103.74))
    save_load("2026-11-16", [near, far, stopped, BARE_STUDY])
    save_load("2026-11-17", [updated(stopped, "COMPLETED", "2026-11-17")])
    transform.build()

    response = TestClient(app).post("/trials", json={"condition": "alopecia", **SINGAPORE})

    assert response.status_code == 200
    body = response.json()
    assert body["data_as_of"] == "2026-11-17T00:00:00"
    found = [
        (t["nct_id"], t["city"], t["is_recruiting"], t["overall_status"], t["distance_km"])
        for t in body["trials"]
    ]
    assert found == [
        ("NCT00000011", "Singapore", True, "RECRUITING", 0.0),
        ("NCT00000012", "London", True, "RECRUITING", 10855.5),
        ("NCT00000013", "Johor Bahru", False, "COMPLETED", 25.4),
    ]


def test_rejects_an_impossible_location() -> None:
    response = TestClient(app).post(
        "/trials", json={"condition": "alopecia", "latitude": 91, "longitude": 0}
    )
    assert response.status_code == 422


def test_says_when_the_tables_are_not_built() -> None:
    response = TestClient(app).post("/trials", json={"condition": "alopecia", **SINGAPORE})
    assert response.status_code == 503
