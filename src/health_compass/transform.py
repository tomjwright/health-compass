"""Build the trial tables in DuckDB: load the new raw loads with Python, then transform with dbt.

The raw tables and staging are append-only: they keep every load so far, and each build adds only
the new loads. So the build works on a copy of the live database file. Only if every model and
test passes does the copy replace the live file, so readers (the API, the agents) always see a
complete, tested database. They open it read-only; DuckDB allows one writer, and this keeps the
writer off it.

    uv run --env-file .env python -m health_compass.transform [--full-refresh]

`--full-refresh` starts from an empty file and replays every raw load, for example after a change
to staging.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
from opentelemetry import trace

from health_compass.ingest.clinicaltrials import raw_dir
from health_compass.ingest.common import raw_warehouse_path
from health_compass.load import load_raw
from health_compass.telemetry import setup_logging, setup_tracing

TRANSFORM_DIR = Path(__file__).resolve().parents[2] / "transform"

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def warehouse_path() -> Path:
    return Path(os.environ.get("DUCKDB_PATH", "data/health_compass.duckdb"))


def dbt(*args: str, env: dict[str, str] | None = None) -> bool:
    """Run a dbt command in its own process (so DuckDB closes the file before the swap)."""
    command = [sys.executable, "-m", "dbt.cli.main", *args, f"--project-dir={TRANSFORM_DIR}"]
    command.append(f"--profiles-dir={TRANSFORM_DIR}")
    return subprocess.run(command, env=env).returncode == 0  # noqa: S603 (fixed command)


def approvals_loaded() -> bool:
    """Whether dlt has loaded both openFDA and EMA into raw.duckdb. Creates an empty file if not,
    because dbt attaches it read-only."""
    path = raw_warehouse_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as db:
        schemas = db.execute("select schema_name from information_schema.schemata").fetchall()
    return ("openfda",) in schemas and ("ema",) in schemas


def build(full_refresh: bool = False) -> None:
    manifests = [json.loads(path.read_text()) for path in raw_dir().glob("*/manifest.json")]
    if not any(manifest["complete"] for manifest in manifests):
        raise SystemExit("No complete ClinicalTrials.gov load yet: run the ingestion first.")
    live = warehouse_path()
    new = live.with_name(live.name + ".new")
    new.unlink(missing_ok=True)  # left over from a failed build
    live.parent.mkdir(parents=True, exist_ok=True)
    if live.exists() and not full_refresh:
        shutil.copyfile(live, new)
    # A failed `dbt deps` can leave the folder behind, so check for the package itself.
    installed = (TRANSFORM_DIR / "dbt_packages/dbt_utils/dbt_project.yml").exists()
    if not installed and not dbt("deps"):
        raise SystemExit("dbt deps failed: could not install dbt_utils (see package-lock.yml).")
    load_raw(new)
    raw = str(raw_warehouse_path().resolve())
    command = ["build"]
    if not approvals_loaded():
        log.warning("no openFDA and EMA data in %s, so the approvals tables are skipped", raw)
        command += ["--exclude", "source:openfda+", "source:ema+"]
    with tracer.start_as_current_span("dbt.build", attributes={"full_refresh": full_refresh}):
        log.info("building %s (full refresh: %s)", live, full_refresh)
        env = os.environ | {"DBT_DUCKDB_PATH": str(new), "RAW_DUCKDB_PATH": raw}
        passed = dbt(*command, env=env)
    if not passed:
        raise SystemExit(f"dbt build failed, so {live} was left as it was.")
    new.replace(live)
    log.info("built %s", live)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--full-refresh", action="store_true", help="replay every raw load")
    args = parser.parse_args()
    setup_logging()
    setup_tracing("health-compass-transform")
    build(args.full_refresh)


if __name__ == "__main__":
    main()
