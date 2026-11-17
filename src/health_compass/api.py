"""The HTTP API the Next.js frontend calls.

Run it with: uv run --env-file .env uvicorn health_compass.api:app --reload
"""

import logging

from fastapi import FastAPI
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from health_compass.telemetry import setup_logging, setup_tracing

setup_logging()
setup_tracing("health-compass-api")
log = logging.getLogger(__name__)

app = FastAPI(title="Health Compass")
FastAPIInstrumentor.instrument_app(app)


@app.get("/health")
def health() -> dict[str, str]:
    log.info("health check")
    return {"status": "ok"}
