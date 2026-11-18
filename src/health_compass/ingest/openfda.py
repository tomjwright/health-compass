"""Land US drug approvals from openFDA as raw JSON, as a durable DBOS workflow.

Two of openFDA's bulk downloads, saved as gzipped JSON lines (one record per line) under
`$RAW_DATA_DIR/openfda/<workflow id>/`:
- `applications-NN.jsonl.gz`: every FDA drug application (Drugs@FDA), with its products and
  approval history;
- `labels-NN.jsonl.gz`: what each drug label says the drug is for (`indications_and_usage`).
  The labels are 14 zip files of about 130 MB each, so each one is streamed and only a few fields
  are kept.

Each file is one DBOS step, so a crash resumes from the file it was on. A run is named after the
day it runs, so running again the same day returns the saved result. `manifest.json` is last.

    uv run --env-file .env python -m health_compass.ingest.openfda
"""

import logging
import os
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import ijson  # type: ignore[import-untyped]
from dbos import DBOS
from opentelemetry import trace

from health_compass.ingest import common
from health_compass.ingest.common import MAX_ATTEMPTS
from health_compass.telemetry import setup_logging, setup_tracing

DOWNLOADS = "https://api.fda.gov/download.json"
LABEL_OPENFDA_FIELDS = ("application_number", "brand_name", "generic_name", "product_type")

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def label_indications(labels: Iterator[Any]) -> Iterator[dict[str, Any]]:
    """Keep only labels that say what the drug is for and name its FDA application."""
    for label in labels:
        openfda = label.get("openfda", {})
        if "indications_and_usage" not in label or "application_number" not in openfda:
            continue
        yield {
            "set_id": label.get("set_id"),
            "effective_time": label.get("effective_time"),
            "indications_and_usage": label["indications_and_usage"],
            "openfda": {field: openfda.get(field) for field in LABEL_OPENFDA_FIELDS},
        }


@DBOS.step(retries_allowed=True, max_attempts=MAX_ATTEMPTS)
def list_files() -> dict[str, Any]:
    """The current bulk files: their URLs, and the date openFDA exported them."""
    drug = common.get_json(DOWNLOADS)["results"]["drug"]
    return {
        "export_date": drug["drugsfda"]["export_date"],
        "applications": [part["file"] for part in drug["drugsfda"]["partitions"]],
        "labels": [part["file"] for part in drug["label"]["partitions"]],
    }


@DBOS.step(retries_allowed=True, max_attempts=MAX_ATTEMPTS)
def save_file(run_dir: str, kind: str, part: int, url: str) -> int:
    """Download one zip file and save its records as JSON lines. Returns how many."""
    with tracer.start_as_current_span("openfda.save_file", attributes={"kind": kind}):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "download.zip"
            common.download(url, zip_path)
            with zipfile.ZipFile(zip_path) as archive, archive.open(archive.namelist()[0]) as data:
                # Streamed record by record: a label file is over 1 GB of JSON.
                records = ijson.items(data, "results.item", use_float=True)
                if kind == "labels":
                    records = label_indications(records)
                count = common.save_lines(Path(run_dir) / f"{kind}-{part:02d}.jsonl.gz", records)
        log.info("saved %d %s from file %d", count, kind, part)
        return count


@DBOS.workflow()
def ingest_openfda() -> dict[str, Any]:
    with tracer.start_as_current_span("openfda.ingest"):
        run_dir, started_at = common.start_run("openfda")
        files = list_files()
        counts = {}
        for kind in ("applications", "labels"):
            counts[kind] = 0
            for part, url in enumerate(files[kind], start=1):
                counts[kind] += save_file(run_dir, kind, part, url)
        manifest = {
            "workflow_id": DBOS.workflow_id,
            "loaded_at": started_at,
            "export_date": files["export_date"],
            "counts": counts,
            "complete": True,
        }
        common.write_manifest(run_dir, manifest)
        log.info("ingested openFDA: %s", counts)
        return manifest


def run_load() -> dict[str, Any]:
    return common.run_daily("openfda", ingest_openfda)


def main() -> None:
    setup_logging()
    setup_tracing("health-compass-ingest")
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    run_load()
    DBOS.destroy()


if __name__ == "__main__":
    main()
