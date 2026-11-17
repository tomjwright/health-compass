"""Land every recruiting trial from ClinicalTrials.gov as raw JSON, as a durable DBOS workflow.

Each API page is one DBOS step, saved unchanged (gzipped) under
`$RAW_DATA_DIR/clinicaltrials/<workflow id>/`. A run is named after the version of the data it
loads, so re-running carries on from where the last run for that version stopped (see `run_load`).
`manifest.json` is written last, so a run folder without one is incomplete.

    uv run --env-file .env python -m health_compass.ingest.clinicaltrials [--max-pages N]
"""

import argparse
import gzip
import json
import logging
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dbos import DBOS, SetWorkflowID
from opentelemetry import trace

from health_compass.ingest.schema_check import compare, flatten
from health_compass.telemetry import setup_logging, setup_tracing

API = "https://clinicaltrials.gov/api/v2"
PAGE_SIZE = 1000
SCHEMA_SNAPSHOT = Path(__file__).with_name("clinicaltrials_schema.json")

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)


class SchemaChangeError(Exception):
    """The API removed or retyped fields we have saved; update the models before loading."""


def http_client() -> httpx.Client:
    return httpx.Client(base_url=API, timeout=60, headers={"User-Agent": "health-compass"})


def get(path: str, params: dict[str, str | int] | None = None) -> httpx.Response:
    with http_client() as client:
        response = client.get(path, params=params)
    if response.is_error:
        # A plain error: DBOS saves step errors, and httpx's errors can't be loaded back.
        raise RuntimeError(f"HTTP {response.status_code} from {response.url}")
    return response


def raw_dir() -> Path:
    return Path(os.environ.get("RAW_DATA_DIR", "data/raw")) / "clinicaltrials"


@DBOS.step(retries_allowed=True, max_attempts=3)
def fetch_json(path: str) -> Any:
    with tracer.start_as_current_span("clinicaltrials.fetch", attributes={"path": path}):
        return get(path).json()


@DBOS.step(retries_allowed=True, max_attempts=3)
def save_page(run_dir: str, page: int, page_token: str | None) -> tuple[str | None, int]:
    """Fetch one page of recruiting trials and save it unchanged. Returns (next token, count)."""
    params: dict[str, str | int] = {"filter.overallStatus": "RECRUITING", "pageSize": PAGE_SIZE}
    if page_token:
        params["pageToken"] = page_token
    with tracer.start_as_current_span("clinicaltrials.save_page", attributes={"page": page}):
        response = get("/studies", params)
        path = Path(run_dir) / f"page-{page:05d}.json.gz"
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash mid-write never leaves a truncated page.
        path.with_suffix(".tmp").write_bytes(gzip.compress(response.content))
        path.with_suffix(".tmp").replace(path)
        body = response.json()
        log.info("saved page %d with %d studies", page, len(body["studies"]))
        return body.get("nextPageToken"), len(body["studies"])


@DBOS.step()
def start_run() -> tuple[str, str]:
    """Pick the run folder and load time once, so a resumed or forked run keeps both."""
    return str(raw_dir() / str(DBOS.workflow_id)), datetime.now(UTC).isoformat()


@DBOS.step()
def write_manifest(run_dir: str, manifest: dict[str, Any]) -> None:
    (Path(run_dir) / "manifest.json").write_text(json.dumps(manifest, indent=2))


def check_schema() -> None:
    """Log new fields; stop the run if any saved field was removed or changed type."""
    snapshot = json.loads(SCHEMA_SNAPSHOT.read_text())
    diff = compare(snapshot, flatten(fetch_json("/studies/metadata")))
    if diff.added:
        log.warning("ClinicalTrials.gov added fields: %s", ", ".join(diff.added))
    if diff.is_breaking:
        raise SchemaChangeError(f"removed: {diff.removed}, changed type: {diff.changed}")


@DBOS.workflow()
def ingest_recruiting_trials(
    version: dict[str, str], max_pages: int | None = None
) -> dict[str, Any]:
    with tracer.start_as_current_span("clinicaltrials.ingest"):
        check_schema()
        run_dir, started_at = start_run()
        page, studies, token = 0, 0, None
        while True:
            page += 1
            token, count = save_page(run_dir, page, token)
            studies += count
            if token is None or page == max_pages:
                break
        manifest = {
            "workflow_id": DBOS.workflow_id,
            "loaded_at": started_at,
            "api_version": version["apiVersion"],
            "data_timestamp": version["dataTimestamp"],
            "pages": page,
            "studies": studies,
            "complete": token is None,
        }
        write_manifest(run_dir, manifest)
        log.info("ingested %d recruiting trials in %d pages", studies, page)
        return manifest


def current_version() -> Any:
    """Read the data's version. This runs before any workflow, so it retries by itself."""
    for wait in (1, 2):
        try:
            return get("/version").json()
        except (RuntimeError, httpx.TransportError):
            log.warning("GET /version failed, retrying in %ds", wait)
            time.sleep(wait)
    return get("/version").json()


def run_load(max_pages: int | None = None) -> dict[str, Any]:
    """Load the data ClinicalTrials.gov currently publishes, or carry on loading it.

    The run is named after the data, not the clock: `clinicaltrials-<dataTimestamp>`, then
    `...-attempt-2` and so on after failures. Re-running after a crash resumes the current
    attempt, and after a success returns its result until newer data is published.
    """
    version = current_version()
    base_id = f"clinicaltrials-{version['dataTimestamp']}"
    if max_pages:
        base_id += f"-{max_pages}-pages"
    workflow_id, attempt, previous_id = base_id, 1, ""
    while (status := DBOS.get_workflow_status(workflow_id)) and status.status == "ERROR":
        previous_id, attempt = workflow_id, attempt + 1
        workflow_id = f"{base_id}-attempt-{attempt}"
    failed_steps = []
    if previous_id and not status:
        failed_steps = [step for step in DBOS.list_workflow_steps(previous_id) if step["error"]]
    with SetWorkflowID(workflow_id):
        if failed_steps:
            # Fork from the failed page: the steps before it keep their saved results.
            manifest: dict[str, Any] = DBOS.fork_workflow(
                previous_id, failed_steps[0]["function_id"]
            ).get_result()
            return manifest
        # Otherwise (first attempt, or a failure outside a step such as a schema change) start over.
        return ingest_recruiting_trials(version, max_pages)


def accept_schema() -> None:
    """Save the API's current data model as the new snapshot (after updating the dbt models)."""
    current = flatten(get("/studies/metadata").json())
    SCHEMA_SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-pages", type=int, help="stop early, for a quick local run")
    parser.add_argument("--accept-schema", action="store_true", help="save the current schema")
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    if args.accept_schema:
        accept_schema()
        return

    setup_logging()
    setup_tracing("health-compass-ingest")
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    run_load(args.max_pages)
    DBOS.destroy()


if __name__ == "__main__":
    main()
