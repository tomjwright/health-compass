from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def raw_warehouse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Keep every test away from the real data/raw.duckdb and dlt's pipeline state."""
    monkeypatch.setenv("RAW_DUCKDB_PATH", str(tmp_path / "raw.duckdb"))
    monkeypatch.setenv("DLT_DATA_DIR", str(tmp_path / "dlt"))
