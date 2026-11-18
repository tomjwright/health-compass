"""Load the complete ClinicalTrials.gov raw loads into plain DuckDB tables, for dbt to transform.

Each new load folder adds one row to `raw.clinicaltrials_loads` and one row per trial to
`raw.clinicaltrials_studies`. Loads already in the tables are skipped, so both tables only grow.
Pages are inserted one at a time: a full load is about 900 MB of JSON, too much to read at once.
"""

import json
import logging
import os
from pathlib import Path

import duckdb

from health_compass.ingest.clinicaltrials import raw_dir

log = logging.getLogger(__name__)

CREATE_TABLES = """
create schema if not exists raw;
create table if not exists raw.clinicaltrials_loads (
    load_id varchar primary key, data_timestamp timestamp, mode varchar, since date
);
create table if not exists raw.clinicaltrials_studies (
    load_id varchar not null, study json not null
);
"""

INSERT_STUDIES = """
insert into raw.clinicaltrials_studies
select ?, unnest(studies)
from read_json(?, columns = {studies: 'JSON[]'}, maximum_object_size = 200000000)
"""


def memory_limit() -> str:
    """How much memory DuckDB may use, as in "1GB". dbt reads the same setting."""
    return os.environ.get("DUCKDB_MEMORY_LIMIT", "1GB")


def load_raw(database: Path) -> None:
    with duckdb.connect(str(database), config={"memory_limit": memory_limit()}) as db:
        db.execute(CREATE_TABLES)
        loaded = db.execute("select load_id from raw.clinicaltrials_loads").fetchall()
        loaded_ids = {load_id for (load_id,) in loaded}
        for path in sorted(raw_dir().glob("*/manifest.json")):
            load_id = path.parent.name
            manifest = json.loads(path.read_text())
            # Skip loads already in the tables, and loads cut short by --max-pages.
            if load_id in loaded_ids or not manifest["complete"]:
                continue
            pages = sorted(path.parent.glob("page-*.json.gz"))
            if not pages:
                raise SystemExit(f"{load_id} is marked complete but has no pages.")
            # One transaction, so a crash part-way never leaves half a load in the table.
            db.begin()
            for page in pages:
                db.execute(INSERT_STUDIES, [load_id, str(page)])
            mode, since = manifest.get("mode", "full"), manifest.get("since")
            db.execute(
                "insert into raw.clinicaltrials_loads values (?, ?, ?, ?)",
                [load_id, manifest["data_timestamp"], mode, since],
            )
            db.commit()
            log.info("loaded %s into the raw tables", load_id)
