"""What the ingestion sources share: retries, HTTP and the dlt pipeline into raw.duckdb."""

import logging
import os
import time
from pathlib import Path
from typing import Any

import dlt
import httpx
from dlt.common.schema.typing import TSchemaContractDict

# How many times to try each request before giving up (a 503 or dropped connection is often brief).
MAX_ATTEMPTS = 3
# New tables and columns are added as a source grows; a column changing type stops the load.
SCHEMA_CONTRACT: TSchemaContractDict = {
    "tables": "evolve",
    "columns": "evolve",
    "data_type": "freeze",
}

log = logging.getLogger(__name__)


def raw_warehouse_path() -> Path:
    """The DuckDB file dlt loads the raw data into. dbt attaches it read-only."""
    return Path(os.environ.get("RAW_DUCKDB_PATH", "data/raw.duckdb"))


def pipeline(source: str) -> dlt.Pipeline:
    """A dlt pipeline that loads `source` into its own schema (dataset) in raw.duckdb."""
    raw_warehouse_path().parent.mkdir(parents=True, exist_ok=True)
    return dlt.pipeline(
        pipeline_name=source,
        destination=dlt.destinations.duckdb(str(raw_warehouse_path())),
        dataset_name=source,
    )


def http_client() -> httpx.Client:
    return httpx.Client(
        timeout=300, follow_redirects=True, headers={"User-Agent": "health-compass"}
    )


def with_retries(url: str, attempt_request: Any) -> Any:
    """Call `attempt_request()` up to MAX_ATTEMPTS times, waiting longer after each failure."""
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            return attempt_request()
        except (httpx.HTTPError, RuntimeError) as error:
            if attempt == MAX_ATTEMPTS:
                raise
            log.warning("%s failed (attempt %d of %d): %s", url, attempt, MAX_ATTEMPTS, error)
            time.sleep(2**attempt)


def get_json(url: str, params: dict[str, str | int] | None = None) -> Any:
    def attempt() -> Any:
        with http_client() as client:
            response = client.get(url, params=params)
        if response.is_error:
            raise RuntimeError(f"HTTP {response.status_code} from {url}")
        return response.json()

    return with_retries(url, attempt)


def download(url: str, path: Path) -> None:
    """Stream `url` to `path`, so a large file never has to fit in memory."""

    def attempt() -> None:
        with http_client() as client, client.stream("GET", url) as response:
            if response.is_error:
                raise RuntimeError(f"HTTP {response.status_code} from {url}")
            with path.open("wb") as file:
                for chunk in response.iter_bytes():
                    file.write(chunk)

    with_retries(url, attempt)
