"""Build the tables in DuckDB with dbt, from the raw tables dlt loaded into raw.duckdb.

Staging is append-only: it keeps every trial load so far, and each build adds only the new
loads. So the build works on a copy of the live database file. Only if every model and test passes
does the copy replace the live file, so readers (the API, the agents) always see a
complete, tested database. They open it read-only; DuckDB allows one writer, and this keeps the
writer off it.

    uv run --env-file .env python -m health_compass.transform [--full-refresh]

`--full-refresh` starts from an empty file and replays every raw load, for example after a change
to staging.
"""

import argparse
import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import duckdb
from opentelemetry import trace

from health_compass.ingest.common import raw_warehouse_path
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


def loaded_sources() -> set[str]:
    """The sources dlt has loaded into raw.duckdb (one schema each). Creates an empty file if
    there is none yet, because dbt attaches it read-only."""
    path = raw_warehouse_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect(str(path)) as db:
        schemas = db.execute("select schema_name from information_schema.schemata").fetchall()
    return {schema for (schema,) in schemas}


def build(full_refresh: bool = False) -> None:
    sources = loaded_sources()
    if "clinicaltrials" not in sources:
        raise SystemExit("No ClinicalTrials.gov load yet: run the ingestion first.")
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
    raw = str(raw_warehouse_path().resolve())
    command = ["build"]
    if "openfda" not in sources or "ema" not in sources:
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
