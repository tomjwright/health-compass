# Health Compass

Evidence-backed health insights for anyone. Ask about a condition and get:

- the latest developments and trials,
- the approved treatments with the strongest evidence, every claim cited,
- recruiting trials near you.

It is cautious and precise: answers come only from cited evidence, it never gives personal
dosing advice, and it tells you what to ask your doctor.

## How it works

```
ClinicalTrials.gov ─┐
Europe PMC ─────────┤ DBOS workflows ─► raw JSON ─► dbt (DuckDB) ─► marts ─┐
openFDA, EMA ───────┘   (durable, retried)                                   │
                                                                             ▼
Next.js ◄── FastAPI ◄── LangGraph supervisor ─► trials / evidence / approvals agents
                          │  input check ─► specialists ─► verifier ─► output check
                          └─ OpenTelemetry traces + JSON logs ─► Jaeger / Langfuse
```

- **Data:** each source lands unchanged as JSON, a schema check flags new or removed fields,
  and dbt models it into DuckDB. The finished DuckDB file is swapped in whole, and the API
  and agents open it read-only.
- **Durable execution:** ingestion, per-condition literature fetches and agent runs are DBOS
  workflows on Postgres, so a crashed run resumes from its last finished step.
- **Agents:** a LangGraph supervisor sends work to specialists. Every answer passes an input
  check, a verifier (each claim must match a cited source) and an output check.
- **Release gate:** an eval suite of reference questions runs in CI and blocks a merge when
  scores drop; every live answer is also scored before it is shown.
- **Privacy:** no accounts, no saved health details, and personal details are removed from traces.

## Run it

Needs [uv](https://docs.astral.sh/uv/) and Docker.

```bash
uv sync
cp .env.example .env
docker compose up -d                      # Postgres + Jaeger
uv run --env-file .env uvicorn health_compass.api:app --reload
curl localhost:8000/health                # {"status":"ok"}
```

Traces show up in Jaeger at http://localhost:16686 (service `health-compass-api`).

### Load trials

```bash
uv run --env-file .env python -m health_compass.ingest.clinicaltrials   # all recruiting trials
uv run --env-file .env python -m health_compass.ingest.clinicaltrials --max-pages 2   # quick run
```

This is a DBOS workflow, saved step by step in Postgres. Each page of 1,000 trials is saved
unchanged under `data/raw/clinicaltrials/<workflow id>/` (about 225 MB for all ~65,000),
and `manifest.json` is written last.

Each run is named after the version of the data it loads (`dataTimestamp` from the API's
`/version`, e.g. `clinicaltrials-2026-11-16T10:00:05`), not the time it runs. ClinicalTrials.gov
publishes new data about once a day. Running the command again:

- **after a crash** resumes the run at the next page.
- **after a page failed 3 times** starts a new attempt (`...-attempt-2`) that carries on from
  that page, keeping the pages already saved.
- **after a success** does nothing until newer data is published, then loads that.
- **Schema change:** each run first compares the API's data model with
  `src/health_compass/ingest/clinicaltrials_schema.json`. New fields are logged. Removed or
  retyped fields stop the run. Update the dbt models, then run the command with
  `--accept-schema` and commit the new snapshot.

### Build the trial tables

```bash
uv run --env-file .env python -m health_compass.transform
```

dbt reads the newest complete load (its `manifest.json` says `"complete": true`) and builds
`data/health_compass.duckdb` with the tables `trials`, `trial_conditions`, `trial_interventions`
and `trial_sites` (with latitude and longitude). Every model has an enforced contract (column
names and types), and the build runs dbt's tests. It builds a new file and only swaps it in if
everything passes, so a bad load never replaces good tables. Query them with
`duckdb -readonly data/health_compass.duckdb`.

Each build rebuilds every table from one complete load, which is a snapshot of all recruiting
trials. So there are no incremental models: a trial that stops recruiting simply drops out.

## Roadmap

1. Scaffold: tooling, CI, Docker Compose, OpenTelemetry and JSON logging
2. ClinicalTrials.gov ingestion (DBOS workflow, raw JSON, schema-change check)
3. dbt staging and marts for trials, with contracts and tests
4. Trial finder API (distance search in SQL)
5. Find trials page (Next.js, Leaflet map)
6. openFDA and EMA approvals ingestion and models
7. Europe PMC fetch per condition, embeddings and vector search
8. LangGraph supervisor, trials agent and the checks
9. Evidence agent, approvals agent, writer and verifier
10. Eval harness, CI release gate and runtime gate
11. Ask page with live agent progress

Later: EU CTIS trials, ChEMBL, openFDA adverse events, free hosting.

Health Compass is not medical advice.
