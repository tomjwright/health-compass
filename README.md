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

### Find trials

Once the trial tables are built (see below), `POST /trials` finds trials for a condition, each at
its site nearest the user. Recruiting trials come first, nearest first, then trials that have
stopped recruiting (`is_recruiting: false`, with their `overall_status`). The search is a POST
body, so the condition and location stay out of URLs, logs and traces.

```bash
curl -s localhost:8000/trials -H 'content-type: application/json' \
  -d '{"condition": "alopecia", "latitude": 1.29, "longitude": 103.85, "limit": 3}'
```

The query is plain SQL in `src/health_compass/find_trials.sql`. The distance comes from the
haversine formula. Each request opens its own read-only DuckDB connection, so it reads the latest
build.

### Load trials

```bash
uv run --env-file .env python -m health_compass.ingest.clinicaltrials   # first run: all recruiting trials
uv run --env-file .env python -m health_compass.ingest.clinicaltrials --max-pages 2   # quick run
```

This is a DBOS workflow, saved step by step in Postgres. Each page of 1,000 trials is saved
unchanged under `data/raw/clinicaltrials/<workflow id>/`, and `manifest.json` is written last.

- **First run (`"mode": "full"`):** every recruiting trial, about 65,000 in 66 pages (225 MB).
- **Every later run (`"mode": "incremental"`):** only the trials updated since the day before the
  last complete load (`"since"`), in any status, so we see trials that stop recruiting. That's
  about 1,000 trials in 1–2 pages a day. The day of overlap means a late update is never missed;
  dbt drops the duplicates.

Every load is kept: together they are the history that dbt replays.

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

Python loads each new complete load (its `manifest.json` says `"complete": true`) into two plain
tables in `data/health_compass.duckdb`: `raw.clinicaltrials_loads` and `raw.clinicaltrials_studies`
(one row per trial per load, the JSON unchanged). dbt only transforms, reading those tables as
sources, in three layers:

- `staging`: append-only, one row per trial per load, the raw JSON renamed and typed. Each build
  adds only the loads it hasn't seen yet.
- `intermediate`: `int_trial_versions` keeps one row per version of a trial, then views unnest
  the current version's conditions, interventions and sites.
- `marts`: a star schema for the API and agents, rebuilt from staging on every build.

| Table | One row per |
|---|---|
| `fct_trial_sites` | trial per site: the starting point for "trials near me" |
| `fct_trials` | trial, with its enrollment and last-update date |
| `fct_trial_status_changes` | change in a trial's status, e.g. `RECRUITING` to `COMPLETED` |
| `dim_trials` | version of a trial (SCD type 2): title, phase, eligibility, `overall_status`, `is_recruiting`, `valid_from`, `valid_to`, `is_current` |
| `dim_locations` | site: facility, city, country, latitude and longitude |
| `dim_conditions` + `bridge_trial_conditions` | condition, as the sponsor wrote it or as a MeSH term |
| `dim_interventions` + `bridge_trial_interventions` | drug, device, procedure, ... |
| `dim_sponsors`, `dim_dates` | lead sponsor, day |

Facts and bridges join to dimensions on `*_key` columns (hashes made by `dbt_utils`). Staging and
marts have enforced contracts (column names and types), and the build runs dbt's tests: unique
keys, and every key in a fact or bridge exists in its dimension. It builds a new file and only
swaps it in if everything passes, so a bad load never replaces good tables. Because the raw
tables and staging keep their rows from earlier builds, the new file starts as a copy of the live one. Query them with
`duckdb -readonly data/health_compass.duckdb`, e.g. alopecia trials by distance from Singapore:

```sql
select t.nct_id, l.facility, l.city, t.is_recruiting
from marts.fct_trial_sites
join marts.dim_trials t using (trial_key)
join marts.dim_locations l using (location_key)
where trial_key in (
    select trial_key
    from marts.bridge_trial_conditions
    join marts.dim_conditions using (condition_key)
    where condition_name ilike '%alopecia%'
)
order by (l.latitude - 1.29) ^ 2 + (l.longitude - 103.85) ^ 2
limit 3;
```

**Trial history.** A new version of a trial starts each time ClinicalTrials.gov posts an update
(`last_updated` changes). The previous version gets `valid_to` set to that day. Use
`is_current` for each trial as it is now. The facts, bridges and sites point at current versions.
The tables hold every trial that was recruiting in some load. A trial that stops recruiting stays,
with `is_recruiting = false` and its new `overall_status`, so the app can show it as "Not
recruiting: Completed". Trials we never saw recruiting are left out.

**Rebuild from scratch.** `python -m health_compass.transform --full-refresh` starts from an empty
file and replays every raw load. Use it after changing staging, or if the DuckDB file is lost.

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
