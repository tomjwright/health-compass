import json
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient

from health_compass.api import app


def test_searches_all_recruiting_studies_without_a_location(
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
    first = client.post("/trials/worldwide", json={"condition": "hair loss"})
    assert first.status_code == 200
    assert first.headers["cache-control"] == "no-store"
    assert first.json() == {
        "total_count": 2,
        "next_page_token": "second-page",
        "trials": [
            {
                "nct_id": "NCT00000001",
                "title": "A trial without a site",
                "overall_status": "RECRUITING",
            }
        ],
    }
    last = client.post(
        "/trials/worldwide",
        json={"condition": "hair loss", "page_token": first.json()["next_page_token"]},
    )
    assert last.status_code == 200
    assert last.json()["next_page_token"] is None
    assert last.json()["total_count"] is None
    assert last.json()["trials"][0]["nct_id"] == "NCT00000002"
