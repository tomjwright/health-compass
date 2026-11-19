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
                "recruiting_sites": [],
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


@pytest.mark.parametrize("radius", [100, 1000])
def test_nearby_returns_every_recruiting_match_in_distance_order(
    monkeypatch: pytest.MonkeyPatch,
    radius: int,
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
        assert params["filter.geo"] == [f"distance(0.0,0.0,{radius + 12}km)"]
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
                    study("NCT00000005", ("France", "RECRUITING", 12)),
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
            "radius_km": radius,
        },
    )
    assert response.status_code == 200
    body = response.json()
    expected = ["NCT00000002", "NCT00000001"]
    if radius == 1000:
        expected.append("NCT00000003")
    assert (body["total_count"], body["radius_km"], body["next_page_token"]) == (
        len(expected),
        radius,
        None,
    )
    assert [trial["nct_id"] for trial in body["trials"]] == expected
    nearest = [trial["nearest_site"] for trial in body["trials"]]
    assert [site["longitude"] for site in nearest[:2]] == [0.2, 0.5]
    assert [site["distance_km"] for site in nearest[:2]] == pytest.approx([17.8, 51.15], abs=0.05)


@pytest.mark.parametrize("country", [None, "Turkey (Türkiye)"])
def test_paged_search_returns_recruiting_locations(
    monkeypatch: pytest.MonkeyPatch, country: str | None
) -> None:
    def registry(url: str, timeout: float) -> BytesIO:
        params = parse_qs(urlsplit(url).query)
        assert "filter.geo" not in params
        if country:
            assert params["filter.advanced"] == [
                'SEARCH[Location](AREA[LocationCountry]COVERAGE[FullMatch]"Turkey (Türkiye)"'
                " AND AREA[LocationStatus]RECRUITING)"
            ]
        assert "LocationGeoPoint" in params["fields"][0].split(",")
        study = {
            "protocolSection": {
                "identificationModule": {"nctId": "NCT00000001", "briefTitle": "A study"},
                "statusModule": {"overallStatus": "RECRUITING"},
                "contactsLocationsModule": {
                    "locations": [
                        {
                            "city": "Ankara",
                            "country": "Turkey (Türkiye)",
                            "status": "RECRUITING",
                            "geoPoint": {"lat": 39.93, "lon": 32.86},
                        },
                        {"city": "Berlin", "country": "Germany", "status": "RECRUITING"},
                        {"city": "Izmir", "country": "Turkey (Türkiye)", "status": "COMPLETED"},
                    ],
                },
            }
        }
        return BytesIO(json.dumps({"studies": [study], "totalCount": 1}).encode())

    monkeypatch.setattr("urllib.request.urlopen", registry)
    response = TestClient(app).post(
        "/trials/search", json={"condition": "hair loss", "country": country}
    )
    assert response.status_code == 200
    study = response.json()["trials"][0]
    locations = study["recruiting_sites"]
    assert [site["city"] for site in locations] == (["Ankara"] if country else ["Ankara", "Berlin"])
    assert locations[0]["point"] == {"lat": 39.93, "lon": 32.86}
    assert study["nearest_site"] is None


def test_registry_outage_returns_a_retryable_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def registry(url: str, timeout: float) -> BytesIO:
        raise TimeoutError

    monkeypatch.setattr("urllib.request.urlopen", registry)
    response = TestClient(app).post("/trials/search", json={"condition": "hair loss"})
    assert response.status_code == 502
    assert response.headers["cache-control"] == "no-store"
