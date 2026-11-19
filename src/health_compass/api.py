"""The HTTP API the Next.js frontend calls.

Run it with: uv run --env-file .env uvicorn health_compass.api:app --reload
"""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from typing import Literal

from dbos import DBOS
from fastapi import FastAPI, HTTPException, Response
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field, model_validator

from health_compass import approvals, registry_trials, research, trials
from health_compass.agent import Answer, answer_question, expand_approval_search
from health_compass.telemetry import setup_logging, setup_tracing

setup_logging()
setup_tracing("health-compass-api")
log = logging.getLogger(__name__)

NOT_BUILT = "The trial tables haven't been built yet."
APPROVALS_NOT_BUILT = "The approval tables haven't been built yet."
RESEARCH_UNAVAILABLE = "Europe PMC couldn't be reached. Try again later."
# How long a run (with its question, location and answer) is kept before it is deleted.
RUN_TTL = timedelta(minutes=int(os.environ.get("RUN_TTL_MINUTES", "60")))


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Launching DBOS also resumes any run that was cut off when the API last stopped.
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    cleanup = asyncio.create_task(delete_old_runs_every_minute())
    yield
    cleanup.cancel()
    DBOS.destroy()


app = FastAPI(title="Health Compass", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict[str, str]:
    log.info("health check")
    return {"status": "ok"}


@app.post("/trials/search")
def search_recruiting_trials(
    search: registry_trials.RegistrySearch, response: Response
) -> registry_trials.RegistryResults:
    """Recruiting studies from ClinicalTrials.gov with optional location filters."""
    try:
        results = registry_trials.search_registry(search)
    except registry_trials.RegistryUnavailable:
        raise HTTPException(
            502,
            registry_trials.REGISTRY_UNAVAILABLE,
            headers={"Cache-Control": "no-store"},
        ) from None
    response.headers["Cache-Control"] = "no-store"
    return results


class ApprovalRequest(approvals.ApprovalSearch):
    expand_terms: bool = False


@app.post("/approvals")
def find_approvals(request: ApprovalRequest) -> approvals.ApprovalResults:
    """US and EU medicines approved now for a condition, newest approval first."""
    search = expand_approval_search(request) if request.expand_terms else request
    try:
        results = approvals.find_approvals(search)
    except approvals.ApprovalTablesNotBuiltError:
        raise HTTPException(503, APPROVALS_NOT_BUILT) from None
    if request.expand_terms:
        results.search = search
    return results


@app.post("/research")
def find_papers(search: research.PaperSearch) -> research.PaperResults:
    """The newest systematic reviews and randomised trials of a medicine for a condition."""
    try:
        return research.find_papers(search)
    except research.ResearchUnavailableError:
        raise HTTPException(503, RESEARCH_UNAVAILABLE) from None


class Question(BaseModel):
    """A question in the user's words. The location comes from the app, never from the LLM."""

    question: str = Field(min_length=3, max_length=500, examples=["Any alopecia trials near me?"])
    latitude: float | None = Field(default=None, ge=-90, le=90, examples=[1.29])
    longitude: float | None = Field(default=None, ge=-180, le=180, examples=[103.85])

    @model_validator(mode="after")
    def validate_location(self) -> "Question":
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("Provide both latitude and longitude, or neither.")
        return self


class Run(BaseModel):
    run_id: str
    status: Literal["running", "done"]
    steps_done: list[str]
    answer: Answer | None = None


@app.post("/ask", status_code=202)
def ask(question: Question) -> Run:
    """Starts answering a question about trials near the user, or medicines approved for a
    condition; GET /ask/{run_id} has the answer."""
    handle = DBOS.start_workflow(
        answer_question, question.question, question.latitude, question.longitude
    )
    return Run(run_id=handle.workflow_id, status="running", steps_done=[])


@app.get("/ask/{run_id}")
def ask_result(run_id: str) -> Run:
    """The run's progress, then its answer, until the run is deleted after RUN_TTL."""
    runs = DBOS.list_workflows(workflow_ids=[run_id], name=answer_question.__qualname__)
    if not runs:
        raise HTTPException(404, "No such run: it may have expired.")
    run = runs[0]
    done = steps_done(run_id)
    if run.status in ("PENDING", "ENQUEUED"):
        return Run(run_id=run_id, status="running", steps_done=done)
    if run.status == "SUCCESS":
        return Run(run_id=run_id, status="done", steps_done=done, answer=run.output)
    if isinstance(run.error, registry_trials.RegistryUnavailable):
        raise HTTPException(502, registry_trials.REGISTRY_UNAVAILABLE)
    if isinstance(run.error, trials.TablesNotBuiltError):
        raise HTTPException(503, NOT_BUILT)
    if isinstance(run.error, approvals.ApprovalTablesNotBuiltError):
        raise HTTPException(503, APPROVALS_NOT_BUILT)
    raise HTTPException(500, "The agent failed.")


def steps_done(run_id: str) -> list[str]:
    """The agent's steps that have run so far, in order, e.g. ["route", "find_trials"]."""
    done = []
    for step in DBOS.list_workflow_steps(run_id, load_output=False):
        name = step["function_name"].split(".")[-1]
        if name not in done:
            done.append(name)
    return done


def delete_old_runs(now: datetime) -> None:
    """Deletes runs, with their question, location and answer, once older than RUN_TTL."""
    cutoff = (now - RUN_TTL).isoformat()
    name = answer_question.__qualname__
    old = DBOS.list_workflows(name=name, end_time=cutoff, load_input=False, load_output=False)
    for run in old:
        DBOS.delete_workflow(run.workflow_id)


async def delete_old_runs_every_minute() -> None:
    # A plain task in each API process, not a DBOS schedule: the ingestion CLI shares the DBOS
    # database, and a schedule's runs could go to it, which doesn't have this function.
    while True:
        try:
            await asyncio.to_thread(delete_old_runs, datetime.now(UTC))
        except Exception:
            log.exception("could not delete old runs")
        await asyncio.sleep(60)
