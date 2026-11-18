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

from health_compass.ingest import common, openfda

APPLICATION = {"application_number": "NDA018154", "products": [{"brand_name": "LONITEN"}]}
LABEL = {
    "set_id": "s1",
    "effective_time": "20251008",
    "indications_and_usage": ["Use to regrow hair"],
    "openfda": {"application_number": ["NDA018154"], "package_ndc": ["0000-0000"]},
    "warnings": ["A long section we don't keep"],
}
UNLINKED_LABEL = {"set_id": "s2", "indications_and_usage": ["No application number"]}


def zipped(results: list[dict[str, Any]]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("data.json", json.dumps({"meta": {}, "results": results}))
    return buffer.getvalue()


def fake_openfda(request: httpx.Request) -> httpx.Response:
    url = str(request.url)
    if url == openfda.DOWNLOADS:
        drug = {
            "drugsfda": {
                "export_date": "2026-11-17",
                "partitions": [{"file": "https://f/apps.zip"}],
            },
            "label": {"partitions": [{"file": "https://f/l1.zip"}, {"file": "https://f/l2.zip"}]},
        }
        return httpx.Response(200, json={"results": {"drug": drug}})
    files = {"apps.zip": [APPLICATION], "l1.zip": [LABEL], "l2.zip": [UNLINKED_LABEL]}
    return httpx.Response(200, content=zipped(files[url.rsplit("/", 1)[1]]))


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    transport = httpx.MockTransport(fake_openfda)
    monkeypatch.setattr(common, "http_client", lambda: httpx.Client(transport=transport))
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    DBOS(config={"name": "test", "system_database_url": f"sqlite:///{tmp_path}/dbos.sqlite"})
    DBOS.launch()
    yield
    DBOS.destroy(destroy_registry=False)


def read_lines(path: Path) -> list[Any]:
    return [json.loads(line) for line in gzip.decompress(path.read_bytes()).splitlines()]


def test_saves_applications_and_label_indications_then_the_manifest(api: None) -> None:
    manifest = openfda.run_load()
    run_dir = common.raw_dir("openfda") / manifest["workflow_id"]
    assert read_lines(run_dir / "applications-01.jsonl.gz") == [APPLICATION]
    # Only labels that name an application are kept, with only the fields we use.
    assert read_lines(run_dir / "labels-01.jsonl.gz") == [
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
    assert read_lines(run_dir / "labels-02.jsonl.gz") == []
    assert manifest["counts"] == {"applications": 1, "labels": 1}
    assert json.loads((run_dir / "manifest.json").read_text()) == manifest


def test_running_again_the_same_day_returns_the_saved_load(
    api: None, caplog: pytest.LogCaptureFixture
) -> None:
    first = openfda.run_load()
    caplog.set_level("INFO")
    assert openfda.run_load() == first
    assert "already loaded openfda today" in caplog.text
