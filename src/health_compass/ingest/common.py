"""What every ingestion source shares: retries, run folders, files and the manifest."""

import gzip
import json
import logging
import os
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from dbos import DBOS, SetWorkflowID

# How many times to try each request before giving up (a 503 or dropped connection is often brief).
MAX_ATTEMPTS = 3

log = logging.getLogger(__name__)


def raw_dir(source: str) -> Path:
    return Path(os.environ.get("RAW_DATA_DIR", "data/raw")) / source


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=300, follow_redirects=True, headers={"User-Agent": "health-compass"}
    )


def get_json(url: str) -> Any:
    with http_client() as client:
        response = client.get(url)
    if response.is_error:
        # A plain error: DBOS saves step errors, and httpx's errors can't be loaded back.
        raise RuntimeError(f"HTTP {response.status_code} from {url}")
    return response.json()


def download(url: str, path: Path) -> None:
    """Stream `url` to `path`, so a large file never has to fit in memory."""
    with http_client() as client, client.stream("GET", url) as response:
        if response.is_error:
            raise RuntimeError(f"HTTP {response.status_code} from {url}")
        with path.open("wb") as file:
            for chunk in response.iter_bytes():
                file.write(chunk)


def save_lines(path: Path, records: Iterable[Any]) -> int:
    """Write the records as gzipped JSON lines. Returns how many were written."""
    count = 0
    # Written to a temporary file, then renamed, so a crash never leaves a truncated file.
    with gzip.open(path.with_suffix(".tmp"), "wt", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record) + "\n")
            count += 1
    path.with_suffix(".tmp").replace(path)
    return count


@DBOS.step()
def start_run(source: str) -> tuple[str, str]:
    """Pick the run folder and load time once, so a resumed run keeps both."""
    run_dir = raw_dir(source) / str(DBOS.workflow_id)
    run_dir.mkdir(parents=True, exist_ok=True)
    return str(run_dir), datetime.now(UTC).isoformat()


@DBOS.step()
def write_manifest(run_dir: str, manifest: dict[str, Any]) -> None:
    # Written to a temporary file, then renamed, so a crash never leaves half a manifest.
    path = Path(run_dir) / "manifest.json"
    path.with_suffix(".tmp").write_text(json.dumps(manifest, indent=2))
    path.with_suffix(".tmp").replace(path)


def run_daily(source: str, workflow: Callable[[], dict[str, Any]]) -> dict[str, Any]:
    """Run a source's workflow once a day: `<source>-<date>`, then `...-attempt-2` if it failed."""
    base_id = f"{source}-{datetime.now(UTC).date().isoformat()}"
    workflow_id, attempt = base_id, 1
    while (status := DBOS.get_workflow_status(workflow_id)) and status.status == "ERROR":
        attempt += 1
        workflow_id = f"{base_id}-attempt-{attempt}"
    if status and status.status == "SUCCESS":
        log.info("already loaded %s today: nothing new", source)
    with SetWorkflowID(workflow_id):
        return workflow()
