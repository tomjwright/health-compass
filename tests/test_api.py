from fastapi.testclient import TestClient

from health_compass.api import app

SINGAPORE = {"latitude": 1.29, "longitude": 103.85}


def test_health_returns_ok() -> None:
    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_snapshot_trial_search_is_retired() -> None:
    response = TestClient(app).post("/trials", json={"condition": "alopecia", **SINGAPORE})
    assert response.status_code == 404
