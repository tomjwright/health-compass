"""The HTTP API the Next.js frontend calls.

Run it with: uv run --env-file .env uvicorn health_compass.api:app --reload
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from dbos import DBOS
from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from health_compass import trials
from health_compass.agent import Answer, answer_question
from health_compass.telemetry import setup_logging, setup_tracing

setup_logging()
setup_tracing("health-compass-api")
log = logging.getLogger(__name__)

NOT_BUILT = "The trial tables haven't been built yet."
# How long an uncollected run (with its question and location) is kept before it is deleted.
RUN_TTL = timedelta(minutes=10)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    # Launching DBOS also resumes any run that was cut off when the API last stopped.
    DBOS(config={"name": "health-compass", "system_database_url": os.environ["DATABASE_URL"]})
    DBOS.launch()
    if DBOS.get_schedule("delete-old-runs") is None:
        DBOS.create_schedule(
            schedule_name="delete-old-runs", workflow_fn=delete_old_runs, schedule="* * * * *"
        )
    yield
    DBOS.destroy()


app = FastAPI(title="Health Compass", lifespan=lifespan)
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict[str, str]:
    log.info("health check")
    return {"status": "ok"}


@app.post("/trials")
def find_trials(search: trials.TrialSearch) -> trials.TrialResults:
    """Trials for a condition, nearest first, recruiting ones before those that have stopped."""
    try:
        return trials.find_trials(search)
    except trials.TablesNotBuiltError:
        raise HTTPException(503, NOT_BUILT) from None


class Question(BaseModel):
    """A question in the user's words. The location comes from the app, never from the LLM."""

    question: str = Field(min_length=3, max_length=500, examples=["Any alopecia trials near me?"])
    latitude: float = Field(ge=-90, le=90, examples=[1.29])
    longitude: float = Field(ge=-180, le=180, examples=[103.85])


class Run(BaseModel):
    run_id: str
    status: Literal["running", "done"]
    steps_done: list[str]
    answer: Answer | None = None


@app.post("/ask", status_code=202)
def ask(question: Question) -> Run:
    """Starts answering a question about trials near the user; GET /ask/{run_id} has the answer."""
    handle = DBOS.start_workflow(
        answer_question, question.question, question.latitude, question.longitude
    )
    return Run(run_id=handle.workflow_id, status="running", steps_done=[])


@app.get("/ask/{run_id}")
def ask_result(run_id: str) -> Run:
    """The run's progress, then its answer. Collecting the answer deletes the run."""
    runs = DBOS.list_workflows(workflow_ids=[run_id], name=answer_question.__qualname__)
    if not runs:
        raise HTTPException(404, "No such run: it may have been collected or expired.")
    run = runs[0]
    done = steps_done(run_id)
    if run.status in ("PENDING", "ENQUEUED"):
        return Run(run_id=run_id, status="running", steps_done=done)
    DBOS.delete_workflow(run_id)
    if run.status == "SUCCESS":
        return Run(run_id=run_id, status="done", steps_done=done, answer=run.output)
    if isinstance(run.error, trials.TablesNotBuiltError):
        raise HTTPException(503, NOT_BUILT)
    raise HTTPException(500, "The agent failed.")


def steps_done(run_id: str) -> list[str]:
    """The agent's steps that have run so far, in order, e.g. ["check_input", "find_trials"]."""
    done = []
    for step in DBOS.list_workflow_steps(run_id, load_output=False):
        name = step["function_name"].split(".")[-1]
        if name not in done:
            done.append(name)
    return done


@DBOS.workflow()
def delete_old_runs(scheduled_at: datetime, context: Any) -> None:
    """Deletes runs nobody collected, and this cleanup's own runs, once older than RUN_TTL."""
    cutoff = (scheduled_at - RUN_TTL).isoformat()
    names = [answer_question.__qualname__, delete_old_runs.__qualname__]
    old = DBOS.list_workflows(name=names, end_time=cutoff, load_input=False, load_output=False)
    for run in old:
        if run.workflow_id != DBOS.workflow_id:
            DBOS.delete_workflow(run.workflow_id)
