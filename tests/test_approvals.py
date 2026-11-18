import gzip
import io
import json
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from dbos import DBOS

from health_compass.ingest import approvals

APPLICATION = {"application_number": "NDA018154", "products": [{"brand_name": "LONITEN"}]}
LABEL = {
    "set_id": "s1",
    "effective_time": "20251008",
    "indications_and_usage": ["Use to regrow hair"],
    "openfda": {"application_number": ["NDA018154"], "package_ndc": ["0000-0000"]},
    "warnings": ["A long section we don't keep"],
}
UNLINKED_LABEL = {"set_id": "s2", "indications_and_usage": ["No application number"]}
MEDICINES = [{"category": "Human", "name_of_medicine": "Talmanco"}, {"category": "Veterinary"}]


def zipped(results: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.json", json.dumps({"meta": {}, "results": results}))
    return buffer.getvalue()


def fake_sources(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == approvals.FDA_DOWNLOADS:
        drug = {
            "drugsfda": {
                "export_date": "2026-11-17",
                "partitions": [{"file": "https://f/apps.zip"}],
            },
            "label": {"partitions": [{"file": "https://f/l1.zip"}, {"file": "https://f/l2.zip"}]},
        }
        return httpx.Response(200, json={"results": {"drug": drug}})
    if url == approvals.EMA_MEDICINES:
        return httpx.Response(200, json={"meta": {}, "data": MEDICINES})
    files = {"apps.zip": [APPLICATION], "l1.zip": [LABEL], "l2.zip": [UNLINKED_LABEL]}
    return httpx.Response(200, content=zipped(files[url.rsplit("/", 1)[1]]))


@pytest.fixture
def sources(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    transport = httpx.MockTransport(fake_sources)
    monkeypatch.setattr(approvals, "http_client", lambda: httpx.Client(transport=transport))
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    DBOS(config={"name": "test", "system_database_url": f"sqlite:///{tmp_path}/dbos.sqlite"})
    DBOS.launch()
    yield
    DBOS.destroy(destroy_registry=False)


def read_lines(path: Path) -> list[Any]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def test_saves_each_source_as_json_lines_then_the_manifest(sources: None) -> None:
    manifest = approvals.run_load()
    run_dir = approvals.raw_dir() / manifest["workflow_id"]
    assert read_lines(run_dir / "fda-applications-01.jsonl.gz") == [APPLICATION]
    assert read_lines(run_dir / "ema-medicines.jsonl.gz") == [MEDICINES[0]]
    # Only labels that name an application are kept, with only the fields we use.
    assert read_lines(run_dir / "fda-labels-01.jsonl.gz") == [
        {
            "set_id": "s1",
            "effective_time": "20251008",
            "indications_and_usage": ["Use to regrow hair"],
            "openfda": {
                "application_number": ["NDA018154"],
                "brand_name": None,
                "generic_name": None,
                "product_type": None,
            },
        }
    ]
    assert read_lines(run_dir / "fda-labels-02.jsonl.gz") == []
    assert manifest["counts"] == {"ema_medicines": 1, "fda_applications": 1, "fda_labels": 1}
    assert json.loads((run_dir / "manifest.json").read_text()) == manifest


def test_running_again_the_same_day_returns_the_saved_load(
    sources: None, caplog: pytest.LogCaptureFixture
) -> None:
    first = approvals.run_load()
    caplog.set_level("INFO")
    assert approvals.run_load() == first
    assert "already loaded today's approvals" in caplog.text
