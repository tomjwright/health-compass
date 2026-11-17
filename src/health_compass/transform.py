"""Build the trial tables in DuckDB with dbt, from the latest complete ClinicalTrials.gov load.

dbt builds a new database file next to the live one. Only if every model and test passes does the
new file replace the live one, so readers (the API, the agents) always see a complete, tested
database. They open it read-only; DuckDB allows one writer, and this keeps the writer off it.
"""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from opentelemetry import trace

from health_compass.ingest.clinicaltrials import raw_dir
from health_compass.telemetry import setup_logging, setup_tracing

TRANSFORM_DIR = Path(__file__).resolve().parents[2] / "transform"

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def warehouse_path() -> Path:
    return Path(os.environ.get("DUCKDB_PATH", "data/health_compass.duckdb"))


def latest_complete_load() -> Path:
    """The newest load whose manifest says every page was saved (`--max-pages` runs are not)."""
    loads = []
    for path in raw_dir().glob("*/manifest.json"):
        manifest = json.loads(path.read_text())
        if manifest["complete"]:
            loads.append((manifest["data_timestamp"], manifest["loaded_at"], path.parent))
    if not loads:
        raise SystemExit("No complete ClinicalTrials.gov load yet: run the ingestion first.")
    return max(loads)[2]


def build() -> None:
    load = latest_complete_load()
    live = warehouse_path()
    new = live.with_name(live.name + ".new")
    new.unlink(missing_ok=True)  # left over from a failed build
    live.parent.mkdir(parents=True, exist_ok=True)
    with tracer.start_as_current_span("dbt.build", attributes={"load": load.name}):
        log.info("building %s from %s", live, load)
        # A separate process, so DuckDB closes (and finishes writing) the file before the swap.
        dbt = subprocess.run(  # noqa: S603 (fixed command, no user input)
            [
                sys.executable,
                "-m",
                "dbt.cli.main",
                "build",
                f"--project-dir={TRANSFORM_DIR}",
                f"--profiles-dir={TRANSFORM_DIR}",
                f"--vars={json.dumps({'raw_run_dir': str(load.resolve())})}",
            ],
            env=os.environ | {"DBT_DUCKDB_PATH": str(new)},
        )
    if dbt.returncode != 0:
        raise SystemExit(f"dbt build failed, so {live} was left as it was.")
    new.replace(live)
    log.info("built %s from %s", live, load)


def main() -> None:
    setup_logging()
    setup_tracing("health-compass-transform")
    build()


if __name__ == "__main__":
    main()
