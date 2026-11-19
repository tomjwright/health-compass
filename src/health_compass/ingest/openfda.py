"""Load US drug approvals from openFDA into raw.duckdb with dlt.

Two of openFDA's bulk downloads, each a dlt resource in the `openfda` schema:
- `applications`: every FDA drug application (Drugs@FDA). dlt unnests its lists into child
  tables: `applications__products`, `applications__products__active_ingredients`,
  `applications__submissions`, ... The `openfda` block is kept as JSON: it is a dozen lists of
  codes we don't use, which would otherwise become a dozen tables.
- `labels`: what each drug label says the drug is for (`indications_and_usage`). The labels are
  14 zip files of about 130 MB each, so each is streamed and only a few fields are kept.

openFDA sends a full copy each time, so each run replaces the tables.

    uv run python -m health_compass.ingest.openfda
"""

import logging
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import dlt
import ijson  # type: ignore[import-untyped]

from health_compass.ingest import common
from health_compass.telemetry import setup_logging

DOWNLOADS = "https://api.fda.gov/download.json"
LABEL_OPENFDA_FIELDS = ("application_number", "brand_name", "generic_name", "product_type")

log = logging.getLogger(__name__)


def zipped_records(url: str) -> Iterator[Any]:
    """Download one zip file and stream the records in its `results` array."""
    with tempfile.TemporaryDirectory() as tmp:
        zip_path = Path(tmp) / "download.zip"
        common.download(url, zip_path)
        with zipfile.ZipFile(zip_path) as archive, archive.open(archive.namelist()[0]) as data:
            # Record by record: a label file is over 1 GB of JSON.
            yield from ijson.items(data, "results.item", use_float=True)


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


@dlt.source(name="openfda", schema_contract=common.SCHEMA_CONTRACT)
def openfda_source() -> Any:
    drug = common.get_json(DOWNLOADS)["results"]["drug"]

    @dlt.resource(write_disposition="replace", columns={"openfda": {"data_type": "json"}})
    def applications() -> Iterator[Any]:
        for part in drug["drugsfda"]["partitions"]:
            yield from zipped_records(part["file"])

    @dlt.resource(write_disposition="replace")
    def labels() -> Iterator[Any]:
        for part in drug["label"]["partitions"]:
            yield from label_indications(zipped_records(part["file"]))

    return applications, labels


def run_load() -> Any:
    info = common.pipeline("openfda").run(openfda_source(), loader_file_format="jsonl")
    log.info("loaded openFDA: %s", info)
    return info


def main() -> None:
    setup_logging()
    run_load()


if __name__ == "__main__":
    main()
