"""Load ClinicalTrials.gov trials into raw.duckdb with dlt.

The first load fetches every recruiting trial. Each later load fetches only the trials updated
since the last load, in any status, so we see trials that stop recruiting. Each load appends, so
`clinicaltrials.studies` keeps one row per trial per load (`_dlt_load_id`), which dbt turns into
each trial's history. `clinicaltrials.loads` has one row per load, with the data's version.

Only the parts of each trial that the dbt models read are kept (`keep_used_fields`). dlt unnests
their lists into child tables: conditions, interventions, locations, phases and MeSH headings.

    uv run --env-file .env python -m health_compass.ingest.clinicaltrials [--max-pages N]
"""

import argparse
import logging
from collections.abc import Iterator
from datetime import datetime, timedelta
from typing import Any

import dlt

from health_compass.ingest import common
from health_compass.telemetry import setup_logging, setup_tracing

API = "https://clinicaltrials.gov/api/v2"
PAGE_SIZE = 1000
# The modules the dbt models read. The rest (outcomes, references, contacts, results, ...) would
# become dozens of tables nobody reads.
PROTOCOL_MODULES = (
    "identificationModule",
    "descriptionModule",
    "statusModule",
    "designModule",
    "eligibilityModule",
    "conditionsModule",
)

# The columns the dbt models read, typed. dlt creates them from the first load, even before any
# trial fills them, and the schema contract stops a load whose types differ.
PROTOCOL = "protocol_section__"
STUDY_COLUMNS: dict[str, Any] = {
    PROTOCOL + "identification_module__nct_id": {"data_type": "text"},
    PROTOCOL + "identification_module__brief_title": {"data_type": "text"},
    PROTOCOL + "description_module__brief_summary": {"data_type": "text"},
    PROTOCOL + "status_module__overall_status": {"data_type": "text"},
    PROTOCOL + "status_module__start_date_struct__date": {"data_type": "text"},
    PROTOCOL + "status_module__last_update_post_date_struct__date": {"data_type": "date"},
    PROTOCOL + "design_module__study_type": {"data_type": "text"},
    PROTOCOL + "design_module__enrollment_info__count": {"data_type": "bigint"},
    PROTOCOL + "sponsor_collaborators_module__lead_sponsor__name": {"data_type": "text"},
    PROTOCOL + "sponsor_collaborators_module__lead_sponsor__class": {"data_type": "text"},
    PROTOCOL + "eligibility_module__sex": {"data_type": "text"},
    PROTOCOL + "eligibility_module__minimum_age": {"data_type": "text"},
    PROTOCOL + "eligibility_module__maximum_age": {"data_type": "text"},
    PROTOCOL + "eligibility_module__healthy_volunteers": {"data_type": "bool"},
    PROTOCOL + "eligibility_module__eligibility_criteria": {"data_type": "text"},
}
log = logging.getLogger(__name__)


def keep_used_fields(study: dict[str, Any]) -> dict[str, Any]:
    protocol = study.get("protocolSection", {})
    kept: dict[str, Any] = {}
    for module in PROTOCOL_MODULES:
        if module in protocol:
            kept[module] = protocol[module]
    # From these modules, only one part each.
    parts = [
        ("sponsorCollaboratorsModule", "leadSponsor"),
        ("armsInterventionsModule", "interventions"),
        ("contactsLocationsModule", "locations"),
    ]
    for module, part in parts:
        if part in protocol.get(module, {}):
            kept[module] = {part: protocol[module][part]}
    # Each site's contacts are people's names, phone numbers and emails, which we don't need.
    for site in kept.get("contactsLocationsModule", {}).get("locations", []):
        site.pop("contacts", None)
    trimmed: dict[str, Any] = {"protocolSection": kept}
    conditions = study.get("derivedSection", {}).get("conditionBrowseModule", {})
    if "meshes" in conditions:
        trimmed["derivedSection"] = {"conditionBrowseModule": {"meshes": conditions["meshes"]}}
    return trimmed


def updated_since(last_data_timestamp: str | None) -> str | None:
    """The day before the last load's data date (a day of overlap), or None before any load."""
    if last_data_timestamp is None:
        return None
    return (datetime.fromisoformat(last_data_timestamp) - timedelta(days=1)).date().isoformat()


@dlt.source(name="clinicaltrials", schema_contract=common.SCHEMA_CONTRACT)
def clinicaltrials_source(
    version: dict[str, str],
    max_pages: int | None = None,
    given_studies: list[dict[str, Any]] | None = None,
) -> Any:
    since = updated_since(dlt.current.source_state().get("data_timestamp"))

    @dlt.resource(write_disposition="append", columns=STUDY_COLUMNS)
    def studies() -> Iterator[Any]:
        """Without `since`, the recruiting trials; with it, every trial updated since then."""
        if given_studies is not None:
            for study in given_studies:
                yield keep_used_fields(study)
            loaded_every_page()
            return
        params: dict[str, str | int] = {"filter.overallStatus": "RECRUITING"}
        if since:
            params = {"filter.advanced": f"AREA[LastUpdatePostDate]RANGE[{since},MAX]"}
        params["pageSize"] = PAGE_SIZE
        page = 0
        while True:
            page += 1
            body = common.get_json(f"{API}/studies", params)
            log.info("fetched page %d with %d studies", page, len(body["studies"]))
            for study in body["studies"]:
                yield keep_used_fields(study)
            if "nextPageToken" not in body:
                loaded_every_page()
                return
            if page == max_pages:
                return
            params["pageToken"] = body["nextPageToken"]

    def loaded_every_page() -> None:
        """Saved with the load, so the next load carries on from this one. A --max-pages run
        doesn't save it, so the next run loads every trial again."""
        dlt.current.source_state()["data_timestamp"] = version["dataTimestamp"]

    # Typed here, because dlt only creates a column once a value arrives and `since` starts empty.
    @dlt.resource(write_disposition="append", columns={"since": {"data_type": "date"}})
    def loads() -> Iterator[dict[str, Any]]:
        yield {
            "data_timestamp": version["dataTimestamp"],
            "api_version": version["apiVersion"],
            "mode": "incremental" if since else "full",
            "since": since,
        }

    return studies, loads


def run_load(max_pages: int | None = None) -> Any:
    """Load the data ClinicalTrials.gov currently publishes, unless it is already loaded."""
    version = common.get_json(f"{API}/version")
    pipeline = common.pipeline("clinicaltrials")
    loaded = pipeline.state.get("sources", {}).get("clinicaltrials", {}).get("data_timestamp")
    if loaded == version["dataTimestamp"]:
        log.info("already loaded the data published at %s: nothing new", loaded)
        return None
    info = pipeline.run(clinicaltrials_source(version, max_pages), loader_file_format="jsonl")
    log.info("loaded the data published at %s: %s", version["dataTimestamp"], info)
    return info


def load_studies(studies: list[dict[str, Any]], data_timestamp: str) -> Any:
    """Load the given trials as one load, as if the API had sent them (for the evals and tests)."""
    version = {"dataTimestamp": data_timestamp, "apiVersion": "given"}
    pipeline = common.pipeline("clinicaltrials")
    source = clinicaltrials_source(version, given_studies=studies)
    return pipeline.run(source, loader_file_format="jsonl")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--max-pages", type=int, help="stop early, for a quick local run")
    args = parser.parse_args()
    if args.max_pages is not None and args.max_pages < 1:
        parser.error("--max-pages must be at least 1")
    setup_logging()
    setup_tracing("health-compass-ingest")
    run_load(args.max_pages)


if __name__ == "__main__":
    main()
