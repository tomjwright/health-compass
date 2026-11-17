# AGENTS.md

Guidance for coding agents working in this repo. Humans: start with [README.md](README.md).

## Standards
The workflow rules shared by all of Tom's repos (branches, PR size and descriptions, testing,
secrets) live in his home repo, `tomjwright/home`. Rules for this repo:
- Everything except the frontend is Python. The frontend is Next.js.
- Prefer plain SQL in dbt over data wrangling in Python.
- Answers must come only from cited evidence; never add personal dosing or "you should take X"
  output, and never weaken a check or an eval threshold to make a test pass.
- Never store users' health details or locations; strip personal details before tracing.
- `.env` is git-ignored; CI reads GitHub secrets.

## Checks
```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy                               # strict, src/ only
uv run pytest
uv run pre-commit run --all-files
```

## Commands
```bash
docker compose up -d                      # Postgres (DBOS, LangGraph) + Jaeger (traces)
uv run --env-file .env uvicorn health_compass.api:app --reload
```

## Architecture in one line
Sources → DBOS ingestion workflows → raw JSON → dbt (DuckDB) → marts → LangGraph agents →
FastAPI → Next.js. Logs are JSON with trace IDs; traces go to the OTLP endpoint in `.env`.
