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
uv run --env-file .env python -m health_compass.ingest.clinicaltrials [--max-pages N]
uv run python -m health_compass.ingest.clinicaltrials --accept-schema   # after a schema change
uv run python -m health_compass.transform   # dbt build into data/health_compass.duckdb
```

## Architecture in one line
Sources → DBOS ingestion workflows → raw JSON → dbt (DuckDB) → marts → LangGraph agents →
FastAPI → Next.js. Logs are JSON with trace IDs; traces go to the OTLP endpoint in `.env`.

## Gotchas
- DBOS saves each step's result, including errors, and replays them. `resume` re-raises a
  failed step's error; `fork` from that step re-runs it.
- Run dbt in its own process: in-process, DuckDB keeps small writes in the `.wal` file until the
  connection closes, so swapping in the `.duckdb` file alone loses them.
- Steps must raise errors that can be pickled; httpx's `HTTPStatusError` can't be loaded back.
