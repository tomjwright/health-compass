"""The HTTP API the Next.js frontend calls.

Run it with: uv run --env-file .env uvicorn health_compass.api:app --reload
"""

import logging

from fastapi import FastAPI, HTTPException
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from pydantic import BaseModel, Field

from health_compass import trials
from health_compass.agent import Answer, TrialAgent, chat_model, judge_model
from health_compass.telemetry import setup_logging, setup_tracing

setup_logging()
setup_tracing("health-compass-api")
log = logging.getLogger(__name__)

NOT_BUILT = "The trial tables haven't been built yet."

app = FastAPI(title="Health Compass")
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


@app.post("/ask")
def ask(question: Question) -> Answer:
    """Answers a question about trials near the user, through the checks in `agent.py`."""
    try:
        return TrialAgent(chat_model(), judge_model()).ask(
            question.question, question.latitude, question.longitude
        )
    except trials.TablesNotBuiltError:
        raise HTTPException(503, NOT_BUILT) from None
