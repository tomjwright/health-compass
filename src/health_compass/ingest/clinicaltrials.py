"""Land every recruiting trial from ClinicalTrials.gov as raw JSON, as a durable DBOS workflow.

Each API page is one DBOS step, saved unchanged (gzipped) under
`$RAW_DATA_DIR/clinicaltrials/<workflow id>/`. If the process dies, re-running the same day's
workflow skips the pages already saved and carries on from the next one. If a page failed every
retry, re-running forks the workflow from that page. `manifest.json` is written last, so a run
folder without one is incomplete.

    uv run --env-file .env python -m health_compass.ingest.clinicaltrials [--max-pages N]
"""

import argparse
import gzip
import json
import logging
import os
from datetime import UTC, date, datetime
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
        Path(run_dir).mkdir(parents=True, exist_ok=True)
        (Path(run_dir) / f"page-{page:05d}.json.gz").write_bytes(gzip.compress(response.content))
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
def ingest_recruiting_trials(max_pages: int | None = None) -> dict[str, Any]:
    with tracer.start_as_current_span("clinicaltrials.ingest"):
        check_schema()
        version = fetch_json("/version")
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


def accept_schema() -> None:
    """Save the API's current data model as the new snapshot (after updating the dbt models)."""
    current = flatten(get("/studies/metadata").json())
    SCHEMA_SNAPSHOT.write_text(json.dumps(current, indent=2, sort_keys=True) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-pages", type=int, help="stop early, for a quick local run")
    parser.add_argument("--accept-schema", action="store_true", help="save the current schema")
    args = parser.parse_args()
    if args.accept_schema:
        accept_schema()
        return

    setup_logging()
    setup_tracing("health-compass-ingest")
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    # One workflow per day: re-running after a crash resumes it instead of starting over.
    workflow_id = f"clinicaltrials-{date.today().isoformat()}"
    if args.max_pages:
        workflow_id += f"-{args.max_pages}-pages"
    status = DBOS.get_workflow_status(workflow_id)
    if status and status.status == "ERROR":
        # Failed steps are saved too, so re-run from the one that failed, keeping earlier pages.
        failed = next(step for step in DBOS.list_workflow_steps(workflow_id) if step["error"])
        DBOS.fork_workflow(workflow_id, failed["function_id"]).get_result()
    else:
        with SetWorkflowID(workflow_id):
            ingest_recruiting_trials(args.max_pages)
    DBOS.destroy()


if __name__ == "__main__":
    main()
