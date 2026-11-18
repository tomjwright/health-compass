"""Load EU medicines from the European Medicines Agency (EMA) into raw.duckdb with dlt.

EMA's medicines report lists every medicine it has assessed, with its status, authorisation date,
MeSH therapeutic areas and indication. The medicines for people become `ema.medicines`, one row
each. EMA sends a full copy each time, so each run replaces the table.

    uv run python -m health_compass.ingest.ema
"""

import logging
from collections.abc import Iterator
from typing import Any

import dlt

from health_compass.ingest import common
from health_compass.telemetry import setup_logging

MEDICINES = (
    "https://www.ema.europa.eu/en/documents/report/medicines-output-medicines_json-report_en.json"
)

log = logging.getLogger(__name__)


@dlt.resource(write_disposition="replace", schema_contract=common.SCHEMA_CONTRACT)
def medicines() -> Iterator[Any]:
    """The medicines for people, leaving out veterinary ones."""
    for medicine in common.get_json(MEDICINES)["data"]:
        if medicine["category"] == "Human":
            yield medicine


def run_load() -> Any:
    info = common.pipeline("ema").run(medicines())
    log.info("loaded EMA: %s", info)
    return info


def main() -> None:
    setup_logging()
    run_load()


if __name__ == "__main__":
    main()
