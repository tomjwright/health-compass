"""Land drug approvals from openFDA (US) and EMA (EU) as raw JSON, as a durable DBOS workflow.

Each file is saved as gzipped JSON lines, one record per line, under
`$RAW_DATA_DIR/approvals/<workflow id>/`:
- `fda-applications-NN.jsonl.gz`: every FDA drug application (Drugs@FDA), with its products and
  approval history;
- `fda-labels-NN.jsonl.gz`: what each FDA drug label says the drug is for
  (`indications_and_usage`). openFDA's labels are 14 zip files of about 130 MB each, so each one
  is streamed and only these few fields are kept;
- `ema-medicines.jsonl.gz`: every medicine for people that EMA has assessed.

Each file is one DBOS step, so a crash resumes from the file it was on. A run is named after the
day it runs, so running again the same day returns the saved result. `manifest.json` is last.

    uv run --env-file .env python -m health_compass.ingest.approvals
"""

import gzip
import json
import logging
import os
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import ijson  # type: ignore[import-untyped]
from dbos import DBOS, SetWorkflowID
from opentelemetry import trace

from health_compass.ingest.clinicaltrials import MAX_ATTEMPTS, write_manifest
from health_compass.telemetry import setup_logging, setup_tracing

FDA_DOWNLOADS = "https://api.fda.gov/download.json"
EMA_MEDICINES = (
    "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
)
LABEL_OPENFDA_FIELDS = ("application_number", "brand_name", "generic_name", "product_type")

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=300, follow_redirects=True, headers={"User-Agent": "health-compass"}
    )


def raw_dir() -> Path:
    return Path(os.environ.get("RAW_DATA_DIR", "data/raw")) / "approvals"


def download(url: str, path: Path) -> None:
    """Stream `url` to `path`, so a large file never has to fit in memory."""
    with http_client() as client, client.stream("GET", url) as response:
        if response.is_error:
            # A plain error: DBOS saves step errors, and httpx's errors can't be loaded back.
            raise RuntimeError(f"HTTP {response.status_code} from {url}")
        with path.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)


def save_lines(path: Path, records: Iterable[Any]) -> int:
    """Write the records as gzipped JSON lines. Returns how many were written."""
    count = 0
    # Write then rename, so a crash mid-write never leaves a truncated file.
    with gzip.open(path.with_suffix(".tmp"), "wt", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record) + "\n")
            count += 1
    path.with_suffix(".tmp").replace(path)
    return count


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
def fda_files() -> dict[str, Any]:
    """The current openFDA bulk files: their URLs, and the date openFDA exported them."""
    with http_client() as client:
        response = client.get(FDA_DOWNLOADS)
    if response.is_error:
        raise RuntimeError(f"HTTP {response.status_code} from {FDA_DOWNLOADS}")
    drug = response.json()["results"]["drug"]
    return {
        "export_date": drug["drugsfda"]["export_date"],
        "applications": [part["file"] for part in drug["drugsfda"]["partitions"]],
        "labels": [part["file"] for part in drug["label"]["partitions"]],
    }


@DBOS.step(retries_allowed=True, max_attempts=MAX_ATTEMPTS)
def save_fda_file(run_dir: str, kind: str, part: int, url: str) -> int:
    """Download one openFDA zip file and save its records as JSON lines. Returns the count."""
    with tracer.start_as_current_span("approvals.save_fda_file", attributes={"kind": kind}):
        with tempfile.TemporaryDirectory() as tmp:
            zip_path = Path(tmp) / "download.zip"
            download(url, zip_path)
            with zipfile.ZipFile(zip_path) as archive, archive.open(archive.namelist()[0]) as data:
                # Streamed record by record: a label file is over 1 GB of JSON.
                records = ijson.items(data, "results.item", use_float=True)
                if kind == "labels":
                    records = label_indications(records)
                count = save_lines(Path(run_dir) / f"fda-{kind}-{part:02d}.jsonl.gz", records)
        log.info("saved %d FDA %s from file %d", count, kind, part)
        return count


@DBOS.step(retries_allowed=True, max_attempts=MAX_ATTEMPTS)
def save_ema_medicines(run_dir: str) -> int:
    with http_client() as client:
        response = client.get(EMA_MEDICINES)
    if response.is_error:
        raise RuntimeError(f"HTTP {response.status_code} from {EMA_MEDICINES}")
    medicines = []
    for medicine in response.json()["data"]:
        if medicine["category"] == "Human":
            medicines.append(medicine)
    count = save_lines(Path(run_dir) / "ema-medicines.jsonl.gz", medicines)
    log.info("saved %d EMA medicines", count)
    return count


@DBOS.step()
def start_run() -> tuple[str, str]:
    """Pick the run folder and load time once, so a resumed run keeps both."""
    run_dir = raw_dir() / str(DBOS.workflow_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir), datetime.now(UTC).isoformat()


@DBOS.workflow()
def ingest_approvals() -> dict[str, Any]:
    with tracer.start_as_current_span("approvals.ingest"):
        run_dir, started_at = start_run()
        files = fda_files()
        counts = {"ema_medicines": save_ema_medicines(run_dir)}
        for kind in ("applications", "labels"):
            counts[f"fda_{kind}"] = 0
            for part, url in enumerate(files[kind], start=1):
                counts[f"fda_{kind}"] += save_fda_file(run_dir, kind, part, url)
        manifest = {
            "workflow_id": DBOS.workflow_id,
            "loaded_at": started_at,
            "fda_export_date": files["export_date"],
            "counts": counts,
            "complete": True,
        }
        write_manifest(run_dir, manifest)
        log.info("ingested approvals: %s", counts)
        return manifest


def run_load() -> dict[str, Any]:
    """Load today's approvals: `approvals-<date>`, then `...-attempt-2` and so on after failures."""
    base_id = f"approvals-{datetime.now(UTC).date().isoformat()}"
    workflow_id, attempt = base_id, 1
    while (status := DBOS.get_workflow_status(workflow_id)) and status.status == "ERROR":
        attempt += 1
        workflow_id = f"{base_id}-attempt-{attempt}"
    if status and status.status == "SUCCESS":
        log.info("already loaded today's approvals: nothing new")
    with SetWorkflowID(workflow_id):
        return ingest_approvals()


def main() -> None:
    setup_logging()
    setup_tracing("health-compass-ingest")
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    run_load()
    DBOS.destroy()


if __name__ == "__main__":
    main()
