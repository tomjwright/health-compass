# AGENTS.md

Guidance for coding agents working in this repo. Humans: start with [README.md](README.md).

## Standards
The workflow rules shared by all of Tom's repos (branches, PR size and descriptions, testing,
secrets) live in his home repo, `tomjwright/home`. Rules for this repo:
- Everything except the frontend is Python. The frontend is Next.js.
- Prefer plain SQL in dbt over data wrangling in Python.
- Answers must come only from cited evidence; never add personal dosing or "you should take X"
  output, and never weaken a check or an eval threshold to make a test pass.
- Never store users' health details or locations, except in a durable `/ask` run, which is
  deleted after `RUN_TTL_MINUTES` (60 by default). Strip personal details before tracing.
- `.env` is git-ignored; CI reads GitHub secrets.

## Checks
```bash
uv sync
uv run ruff check . && uv run ruff format --check .
uv run mypy                               # strict, src/ only
uv run pytest
uv run pre-commit run --all-files
npm --prefix web ci                       # when the frontend changed
npm --prefix web run lint && npm --prefix web run build
actionlint && zizmor --offline .github/workflows
```

## Commands
```bash
docker compose up -d                      # Postgres (DBOS, LangGraph) + Jaeger (traces)
uv run --env-file .env uvicorn health_compass.api:app --reload
uv run --env-file .env python -m health_compass.ingest.clinicaltrials [--max-pages N]
uv run --env-file .env python -m health_compass.ingest.openfda   # US approvals and labels, once a day
uv run --env-file .env python -m health_compass.ingest.ema       # EU medicines, once a day
uv run --env-file .env python -m health_compass.transform   # dbt build into data/health_compass.duckdb
uv run --env-file .env python -m health_compass.transform --full-refresh   # replay every raw load
```

## Architecture in one line
ClinicalTrials.gov, openFDA, EMA → dlt → `data/raw.duckdb` → dbt (DuckDB) → marts → LangGraph agents →
FastAPI → Next.js. Logs are JSON with trace IDs; traces go to the OTLP endpoint in `.env`.

## Gotchas
- Models that read `raw.duckdb` must be tables: a view would point at `raw_data`, which readers
  don't attach.
- DBOS (agent runs only) saves each step's result, including errors, and replays them. `resume` re-raises a
  failed step's error; `fork` from that step re-runs it.
- Run dbt in its own process: in-process, DuckDB keeps small writes in the `.wal` file until the
  connection closes, so swapping in the `.duckdb` file alone loses them.
- Staging is an append-only incremental model: after changing its columns or parsing, run the
  transform with `--full-refresh`, or old rows keep the old logic.
- Readers (API, agents) open a read-only DuckDB connection per request and close it. A long-lived
  connection keeps reading the old file after a build swaps in a new one.
- dlt only creates a column once a value for it arrives, so a test fixture (or a source) without a
  field breaks the staging model that reads it.
- dlt's `[duckdb]` extra pins DuckDB below 2.0; depend on `dlt` and `duckdb>=2.0` separately.
- Steps must raise errors that can be pickled; httpx's `HTTPStatusError` can't be loaded back.
