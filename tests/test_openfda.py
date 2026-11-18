import io
import json
import zipfile
from pathlib import Path
from typing import Any

import duckdb
import httpx
import pytest

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


@pytest.fixture(autouse=True)
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    transport = httpx.MockTransport(fake_openfda)
    monkeypatch.setattr(common, "http_client", lambda: httpx.Client(transport=transport))
    monkeypatch.setenv("RAW_DUCKDB_PATH", str(tmp_path / "raw.duckdb"))
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))


def query(sql: str) -> list[tuple[Any, ...]]:
    with duckdb.connect(str(common.raw_warehouse_path()), read_only=True) as db:
        return db.execute(sql).fetchall()


def test_loads_applications_unnested_and_only_linked_label_indications() -> None:
    openfda.run_load()
    assert query("select application_number from openfda.applications") == [("NDA018154",)]
    assert query("select brand_name from openfda.applications__products") == [("LONITEN",)]
    # Only labels that name an application are kept, without the fields we don't use.
    assert query("select set_id, effective_time from openfda.labels") == [("s1", "20251008")]
    assert query("select value from openfda.labels__indications_and_usage") == [
        ("Use to regrow hair",)
    ]
    columns = query(
        "select column_name from information_schema.columns where table_name = 'labels'"
    )
    assert ("warnings",) not in columns


def test_a_second_load_replaces_the_first() -> None:
    openfda.run_load()
    openfda.run_load()
    assert query("select count(*) from openfda.applications") == [(1,)]
    assert query("select count(*) from openfda.applications__products") == [(1,)]
