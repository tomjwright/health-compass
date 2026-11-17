"""The HTTP API the Next.js frontend calls.

Run it with: uv run --env-file .env uvicorn health_compass.api:app --reload
"""

import logging
from datetime import datetime
from pathlib import Path

import duckdb
from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from health_compass.telemetry import setup_logging, setup_tracing
from health_compass.transform import warehouse_path

setup_logging()
setup_tracing("health-compass-api")
log = logging.getLogger(__name__)

FIND_TRIALS = (Path(__file__).parent / "find_trials.sql").read_text()

app = FastAPI(title="Health Compass")
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict[str, str]:
    log.info("health check")
    return {"status": "ok"}


class TrialSearch(BaseModel):
    """What the user is looking for. Sent as a POST body, so it stays out of URLs and traces."""

    condition: str = Field(min_length=2, max_length=100, examples=["alopecia"])
    latitude: float = Field(ge=-90, le=90, examples=[1.29])
    longitude: float = Field(ge=-180, le=180, examples=[103.85])
    limit: int = Field(default=20, ge=1, le=100)


class Trial(BaseModel):
    """A trial at its site nearest the user. `is_recruiting` is false once it has stopped."""

    nct_id: str
    title: str | None
    overall_status: str
    is_recruiting: bool
    facility: str | None
    city: str | None
    country: str | None
    distance_km: float


class TrialResults(BaseModel):
    data_as_of: datetime
    trials: list[Trial]


@app.post("/trials")
def find_trials(search: TrialSearch) -> TrialResults:
    """Trials for a condition, nearest first, recruiting ones before those that have stopped."""
    database = warehouse_path()
    if not database.exists():
        raise HTTPException(503, "The trial tables haven't been built yet.")
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
