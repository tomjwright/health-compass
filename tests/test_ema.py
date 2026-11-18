import gzip
import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
from dbos import DBOS

from health_compass.ingest import common, ema

MEDICINES = [{"category": "Human", "name_of_medicine": "Talmanco"}, {"category": "Veterinary"}]


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": MEDICINES}))
    monkeypatch.setattr(common, "http_client", lambda: httpx.Client(transport=transport))
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    DBOS(config={"name": "test", "system_database_url": f"sqlite:///{tmp_path}/dbos.sqlite"})
    DBOS.launch()
    yield
    DBOS.destroy(destroy_registry=False)


def test_saves_the_medicines_for_people_then_the_manifest(api: None) -> None:
    manifest = ema.run_load()
    run_dir = common.raw_dir("ema") / manifest["workflow_id"]
    lines = gzip.decompress((run_dir / "medicines.jsonl.gz").read_bytes()).splitlines()
    assert [json.loads(line) for line in lines] == [MEDICINES[0]]
    assert manifest["medicines"] == 1
    assert json.loads((run_dir / "manifest.json").read_text()) == manifest
