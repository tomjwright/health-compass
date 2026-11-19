import json
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from health_compass.api import app


def test_searches_recruiting_studies_worldwide_without_a_location(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def registry(url: str, timeout: float) -> BytesIO:
        request = urlsplit(url)
        assert request.netloc == "clinicaltrials.gov"
        assert request.path == "/api/v2/studies"
        params = parse_qs(request.query)
        assert params.pop("query.cond") == ["hair loss"]
        assert params.pop("filter.overallStatus") == ["RECRUITING"]
        assert params.pop("countTotal") == ["true"]
        params.pop("pageSize")
        params.pop("fields")
        page = params.pop("pageToken", [])
        assert not params
        nct_id = "NCT00000002" if page == ["second-page"] else "NCT00000001"
        study = {
            "protocolSection": {
                "identificationModule": {"nctId": nct_id, "briefTitle": "A trial without a site"},
                "statusModule": {"overallStatus": "RECRUITING"},
            }
        }
        body = (
            {"studies": [study]}
            if page
            else {
                "studies": [study],
                "totalCount": 2,
                "nextPageToken": "second-page",
            }
        )
        return BytesIO(json.dumps(body).encode())

    # Patch the network only, leaving the route, request construction and decoding in use.
    monkeypatch.setattr("urllib.request.urlopen", registry)
    client = TestClient(app)
    first = client.post("/trials/search", json={"condition": "hair loss"})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "total_count": 2,
        "next_page_token": "second-page",
        "radius_km": None,
        "trials": [
            {
                "nct_id": "NCT00000001",
                "title": "A trial without a site",
                "overall_status": "RECRUITING",
                "nearest_site": None,
            }
        ],
    }
    last = client.post(
        "/trials/search",
        json={"condition": "hair loss", "page_token": first.json()["next_page_token"]},
    )
    assert last.status_code == 200
    assert last.json()["next_page_token"] is None
    assert last.json()["total_count"] is None
    assert last.json()["trials"][0]["nct_id"] == "NCT00000002"


def test_nearby_returns_every_recruiting_match_in_distance_order(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def study(nct_id: str, *sites: tuple[str, str, float]) -> dict[str, object]:
        locations = [
            {"country": country, "status": status, "geoPoint": {"lat": 0.04, "lon": longitude}}
            for country, status, longitude in sites
        ]
        return {
            "protocolSection": {
                "identificationModule": {"nctId": nct_id, "briefTitle": nct_id},
                "statusModule": {"overallStatus": "RECRUITING"},
                "contactsLocationsModule": {"locations": locations},
            },
        }

    def registry(url: str, timeout: float) -> BytesIO:
        params = parse_qs(urlsplit(url).query)
        assert params["filter.geo"] == ["distance(0.0,0.0,112km)"]
        assert params["filter.advanced"] == [
            'SEARCH[Location](AREA[LocationCountry]COVERAGE[FullMatch]"France"'
            " AND AREA[LocationStatus]RECRUITING)"
        ]
        if "pageToken" in params:
            assert params["pageToken"] == ["second-page"]
            body = {"studies": [study("NCT00000002", ("France", "RECRUITING", 0.2))]}
        else:
            body = {
                "studies": [
                    study(
                        "NCT00000001",
                        ("France", "NOT_YET_RECRUITING", 0.05),
                        ("Germany", "RECRUITING", 0.05),
                        ("France", "RECRUITING", 0.5),
                    ),
                    study("NCT00000003", ("France", "RECRUITING", 1)),
                    study("NCT00000004", ("Germany", "RECRUITING", 0.05)),
                ],
                "nextPageToken": "second-page",
            }
        return BytesIO(json.dumps(body).encode())

    monkeypatch.setattr("urllib.request.urlopen", registry)
    response = TestClient(app).post(
        "/trials/search",
        json={
            "condition": "hair loss",
            "country": "France",
            "origin": {"latitude": 0.04, "longitude": 0.04},
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert (body["total_count"], body["radius_km"], body["next_page_token"]) == (2, 100, None)
    assert [trial["nct_id"] for trial in body["trials"]] == ["NCT00000002", "NCT00000001"]
    nearest = [trial["nearest_site"] for trial in body["trials"]]
    assert [site["longitude"] for site in nearest] == [0.2, 0.5]
    assert [site["distance_km"] for site in nearest] == pytest.approx([17.8, 51.15], abs=0.05)


def test_country_search_can_return_no_studies(monkeypatch: pytest.MonkeyPatch) -> None:
    def registry(url: str, timeout: float) -> BytesIO:
        params = parse_qs(urlsplit(url).query)
        assert "filter.geo" not in params
        assert params["filter.advanced"] == [
            'SEARCH[Location](AREA[LocationCountry]COVERAGE[FullMatch]"Turkey (Türkiye)"'
            " AND AREA[LocationStatus]RECRUITING)"
        ]
        return BytesIO(b'{"studies": [], "totalCount": 0}')

    monkeypatch.setattr("urllib.request.urlopen", registry)
    response = TestClient(app).post(
        "/trials/search", json={"condition": "hair loss", "country": "Turkey (Türkiye)"}
    )
    assert response.status_code == 200
    assert response.json() == {
        "trials": [],
        "total_count": 0,
        "next_page_token": None,
        "radius_km": None,
    }


def test_registry_outage_returns_a_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def registry(url: str, timeout: float) -> BytesIO:
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", registry)
    response = TestClient(app).post("/trials/search", json={"condition": "hair loss"})
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
