"""Land EU medicines from the European Medicines Agency (EMA) as raw JSON, as a DBOS workflow.

EMA's medicines report lists every medicine it has assessed, with its status, authorisation date,
MeSH therapeutic areas and indication. The medicines for people are saved as gzipped JSON lines
(one medicine per line) in `$RAW_DATA_DIR/ema/<workflow id>/medicines.jsonl.gz`, then
`manifest.json`. A run is named after the day it runs, so running again the same day returns the
saved result.

    uv run --env-file .env python -m health_compass.ingest.ema
"""

import logging
import os
from pathlib import Path
from typing import Any

from dbos import DBOS
from opentelemetry import trace

from health_compass.ingest import common
from health_compass.ingest.common import MAX_ATTEMPTS
from health_compass.telemetry import setup_logging, setup_tracing

MEDICINES = (
    "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
)

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


@DBOS.step(retries_allowed=True, max_attempts=MAX_ATTEMPTS)
def save_medicines(run_dir: str) -> int:
    """Save the medicines for people, leaving out veterinary ones. Returns how many."""
    medicines = []
    for medicine in common.get_json(MEDICINES)["data"]:
        if medicine["category"] == "Human":
            medicines.append(medicine)
    return common.save_lines(Path(run_dir) / "medicines.jsonl.gz", medicines)


@DBOS.workflow()
def ingest_ema() -> dict[str, Any]:
    with tracer.start_as_current_span("ema.ingest"):
        run_dir, started_at = common.start_run("ema")
        medicines = save_medicines(run_dir)
        manifest = {
            "workflow_id": DBOS.workflow_id,
            "loaded_at": started_at,
            "medicines": medicines,
            "complete": True,
        }
        common.write_manifest(run_dir, manifest)
        log.info("ingested %d EMA medicines", medicines)
        return manifest


def run_load() -> dict[str, Any]:
    return common.run_daily("ema", ingest_ema)


def main() -> None:
    setup_logging()
    setup_tracing("health-compass-ingest")
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    run_load()
    DBOS.destroy()


if __name__ == "__main__":
    main()
