"""Find trials for a condition near a location, for the API and the agent.

The search itself is plain SQL in find_trials.sql.
"""

import logging
from datetime import datetime
from pathlib import Path

import duckdb
from pydantic import BaseModel, Field

from health_compass.transform import warehouse_path

log = logging.getLogger(__name__)

FIND_TRIALS = (Path(__file__).parent / "find_trials.sql").read_text()


class TablesNotBuiltError(Exception):
    """The trial tables haven't been built yet: run the transform."""


class TrialSearch(BaseModel):
    """What the user is looking for. Sent as a POST body, so it stays out of URLs and traces."""

    condition: str = Field(min_length=2, max_length=100, examples=["alopecia"])
    latitude: float = Field(ge=-90, le=90, examples=[1.29])
    longitude: float = Field(ge=-180, le=180, examples=[103.85])
    limit: int = Field(default=20, ge=1, le=100)


class Trial(BaseModel):
    """A trial at its nearest recruiting site, or its nearest site if none is recruiting.

    `is_recruiting` is false once the trial has stopped; `site_status` is that site's own status.
    """

    nct_id: str
    title: str | None
    overall_status: str
    is_recruiting: bool
    facility: str | None
    city: str | None
    country: str | None
    site_status: str | None
    distance_km: float


class TrialResults(BaseModel):
    data_as_of: datetime
    trials: list[Trial]


def find_trials(search: TrialSearch) -> TrialResults:
    """Trials for a condition, nearest first, recruiting ones before those that have stopped."""
    database = warehouse_path()
    if not database.exists():
        raise TablesNotBuiltError
    # A new read-only connection per request, so each request reads the latest build.
    with duckdb.connect(str(database), read_only=True) as db:
        rows = db.execute(FIND_TRIALS, search.model_dump()).fetchall()
        names = [column[0] for column in db.description]
        [(data_as_of,)] = db.execute(
            "select max(data_timestamp) from staging.stg_clinicaltrials__loads"
        ).fetchall()
    # The search itself is health and location data, so only the count is logged.
    log.info("found %d trials", len(rows))
    trials = [Trial(**dict(zip(names, row, strict=True))) for row in rows]
    return TrialResults(data_as_of=data_as_of, trials=trials)
