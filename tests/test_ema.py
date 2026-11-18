from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

from health_compass.ingest import common, ema

MEDICINES = [{"category": "Human", "name_of_medicine": "Talmanco"}, {"category": "Veterinary"}]


@pytest.fixture(autouse=True)
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"data": MEDICINES}))
    monkeypatch.setattr(common, "http_client", lambda: httpx.Client(transport=transport))


def query(sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(common.raw_warehouse_path()), read_only=True) as db:
        return db.execute(sql).fetchall()


def test_loads_the_medicines_for_people_and_replaces_them_each_run() -> None:
    ema.run_load()
    ema.run_load()
    assert query("select category, name_of_medicine from ema.medicines") == [("Human", "Talmanco")]
