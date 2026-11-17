import gzip
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from dbos import DBOS, SetWorkflowID

from health_compass.ingest import clinicaltrials

METADATA = [{"name": "protocolSection", "sourceType": "STRUCT", "type": "ProtocolSection"}]
PAGES = {
    None: {"studies": [{"id": 1}, {"id": 2}], "nextPageToken": "p2"},
    "p2": {"studies": [{"id": 3}]},
}


class FakeApi:
    """Stands in for ClinicalTrials.gov and records the requests it gets."""

    def __init__(self) -> None:
        self.metadata = METADATA
        self.failures_left = 0
        self.requests: list[str] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(str(request.url))
        if request.url.path.endswith("/metadata"):
            return httpx.Response(200, json=self.metadata)
        if request.url.path.endswith("/version"):
            return httpx.Response(200, json={"apiVersion": "2.0.5", "dataTimestamp": "2026-11-16"})
        cursor = request.url.params.get("pageToken")
        if cursor == "p2" and self.failures_left:
            self.failures_left -= 1
            return httpx.Response(503)
        return httpx.Response(200, json=PAGES[cursor])


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[FakeApi]:
    fake = FakeApi()
    monkeypatch.setattr(
        clinicaltrials,
        "http_client",
        lambda: httpx.Client(base_url=clinicaltrials.API, transport=httpx.MockTransport(fake)),
    )
    monkeypatch.setattr(clinicaltrials, "SCHEMA_SNAPSHOT", tmp_path / "schema.json")
    (tmp_path / "schema.json").write_text(json.dumps({"protocolSection": "STRUCT:ProtocolSection"}))
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    DBOS(config={"name": "test", "system_database_url": f"sqlite:///{tmp_path}/dbos.sqlite"})
    DBOS.launch()
    yield fake
    DBOS.destroy(destroy_registry=False)


def run(workflow_id: str) -> dict[str, object]:
    with SetWorkflowID(workflow_id):
        return clinicaltrials.ingest_recruiting_trials()


def test_saves_every_page_unchanged_then_the_manifest(api: FakeApi, tmp_path: Path) -> None:
    manifest = run("run-1")
    run_dir = tmp_path / "raw" / "clinicaltrials" / "run-1"
    page_2 = json.loads(gzip.decompress((run_dir / "page-00002.json.gz").read_bytes()))
    assert page_2 == PAGES["p2"]
    assert json.loads((run_dir / "manifest.json").read_text()) == manifest
    assert manifest["studies"] == 3
    assert manifest["complete"] is True


def test_same_workflow_id_does_not_fetch_again(api: FakeApi) -> None:
    first = run("run-1")
    requests_after_first_run = len(api.requests)
    assert run("run-1") == first
    assert len(api.requests) == requests_after_first_run


def test_failed_page_is_retried(api: FakeApi) -> None:
    api.failures_left = 1
    assert run("run-1")["studies"] == 3


def test_forked_run_skips_pages_already_saved(api: FakeApi, tmp_path: Path) -> None:
    api.failures_left = 3  # every attempt at page 2 fails, so the workflow stops
    with pytest.raises(Exception, match="save_page has exceeded its maximum of 3 retries"):
        run("run-1")
    failed_step = next(step for step in DBOS.list_workflow_steps("run-1") if step["error"])

    api.failures_left = 0
    manifest = DBOS.fork_workflow("run-1", failed_step["function_id"]).get_result()
    assert manifest["studies"] == 3
    assert len([url for url in api.requests if url.endswith("pageSize=1000")]) == 1
    assert (tmp_path / "raw" / "clinicaltrials" / "run-1" / "manifest.json").exists()


def test_max_pages_must_be_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sys.argv", ["clinicaltrials", "--max-pages", "0"])
    with pytest.raises(SystemExit):
        clinicaltrials.main()


def test_breaking_schema_change_stops_before_any_page(api: FakeApi) -> None:
    api.metadata = [{"name": "protocolSection", "sourceType": "TEXT", "type": "text"}]
    with pytest.raises(clinicaltrials.SchemaChangeError):
        run("run-1")
    assert not any("/studies?" in url for url in api.requests)
