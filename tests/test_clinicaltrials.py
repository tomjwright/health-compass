from datetime import date
from typing import Any

import duckdb
import httpx
import pytest

from health_compass.ingest import common
from health_compass.ingest.clinicaltrials import run_load

VERSION = {"apiVersion": "2.0.5", "dataTimestamp": "2026-11-16T10:00:05"}


def study(nct_id: str) -> dict[str, Any]:
    return {
        "protocolSection": {
            "identificationModule": {"nctId": nct_id},
            "statusModule": {"lastUpdatePostDateStruct": {"date": "2026-11-15"}},
            "conditionsModule": {"conditions": ["Alopecia"]},
            "contactsLocationsModule": {
                "locations": [{"city": "Singapore", "contacts": [{"name": "Dr A", "phone": "1"}]}]
            },
            "outcomesModule": {"primaryOutcomes": [{"measure": "Hair count"}]},
        },
        "resultsSection": {"participantFlowModule": {"groups": [{"id": "FG000"}]}},
    }


PAGES = {
    None: {"studies": [study("NCT1"), study("NCT2")], "nextPageToken": "p2"},
    "p2": {"studies": [study("NCT3")]},
}


class FakeApi:
    """Stands in for ClinicalTrials.gov and records the requests it gets."""

    def __init__(self) -> None:
        self.version = VERSION
        self.failures_left = 0
        self.requests: list[httpx.URL] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request.url)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json=self.version)
        cursor = request.url.params.get("pageToken")
        if cursor == "p2" and self.failures_left:
            self.failures_left -= 1
            return httpx.Response(503)
        return httpx.Response(200, json=PAGES[cursor])


def no_wait(seconds: float) -> None:
    pass


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> FakeApi:
    fake = FakeApi()

    def fake_client() -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(fake))

    monkeypatch.setattr(common, "http_client", fake_client)
    monkeypatch.setattr(common.time, "sleep", no_wait)
    return fake


def raw(sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(common.raw_warehouse_path()), read_only=True) as db:
        return db.execute(sql).fetchall()


def nct_ids() -> list[tuple[Any, ...]]:
    return raw(
        "select protocol_section__identification_module__nct_id from clinicaltrials.studies"
        " order by 1"
    )


def test_first_load_fetches_the_recruiting_trials_with_only_the_used_modules(
    api: FakeApi,
) -> None:
    run_load()

    assert nct_ids() == [("NCT1",), ("NCT2",), ("NCT3",)]
    assert api.requests[1].params["filter.overallStatus"] == "RECRUITING"
    assert raw("select mode, since from clinicaltrials.loads") == [("full", None)]
    tables = raw(
        "select table_name from information_schema.tables"
        " where table_schema = 'clinicaltrials' and table_name like 'studies%' order by 1"
    )
    assert tables == [
        ("studies",),
        ("studies__protocol_section__conditions_module__conditions",),
        ("studies__protocol_section__contacts_locations_module__locations",),
    ]
    columns = raw(
        "select column_name from information_schema.columns"
        " where table_schema = 'clinicaltrials' and table_name = 'studies'"
        " and (column_name like '%outcomes%' or column_name like 'results%')"
    )
    assert columns == []


def test_does_nothing_when_the_published_data_is_already_loaded(
    api: FakeApi, caplog: pytest.LogCaptureFixture
) -> None:
    run_load()
    api.requests.clear()
    with caplog.at_level("INFO"):
        run_load()

    assert [url.path for url in api.requests] == ["/api/v2/version"]
    assert "nothing new" in caplog.text
    assert len(nct_ids()) == 3


def test_next_load_fetches_the_trials_updated_since_the_day_before(api: FakeApi) -> None:
    run_load()
    api.version = {"apiVersion": "2.0.5", "dataTimestamp": "2026-11-17T09:00:00"}
    run_load()

    since = "AREA[LastUpdatePostDate]RANGE[2026-11-15,MAX]"
    assert api.requests[-1].params["filter.advanced"] == since
    assert raw("select mode, since from clinicaltrials.loads order by mode") == [
        ("full", None),
        ("incremental", date(2026, 11, 15)),
    ]
    assert len(nct_ids()) == 6  # appended: one row per trial per load


def test_retries_a_page_that_fails(api: FakeApi) -> None:
    api.failures_left = 2
    run_load()
    assert len(nct_ids()) == 3


def test_max_pages_stops_early(api: FakeApi) -> None:
    run_load(max_pages=1)
    assert nct_ids() == [("NCT1",), ("NCT2",)]
