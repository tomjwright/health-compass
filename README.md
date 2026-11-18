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

## Run it on your machine

Needs [uv](https://docs.astral.sh/uv/), Docker and the [DuckDB CLI](https://duckdb.org/docs/installation/)
(`brew install duckdb`, or `curl https://install.duckdb.org | sh`). Use the same DuckDB version as
the Python package, `uv run python -c "import duckdb; print(duckdb.__version__)"`, or the CLI may
not open the file.

```bash
uv sync
cp .env.example .env
docker compose up -d                      # Postgres (DBOS) + Jaeger (traces)
uv run --env-file .env python -m health_compass.ingest.clinicaltrials   # all recruiting trials: ~2 min, 225 MB
uv run --env-file .env python -m health_compass.transform               # dbt build: ~1 min, ~1 GB file
duckdb -readonly data/health_compass.duckdb                             # query it (see below)
uv run --env-file .env uvicorn health_compass.api:app --reload          # the API on port 8000
curl localhost:8000/health                # {"status":"ok"}
```

Run the ingestion and the transform again on a later day to load only the trials updated since:
about 1,000 trials in a few seconds. Trial history and status changes appear from this second load.

Traces show up in Jaeger at http://localhost:16686 (service `health-compass-api`).

### Explore the data

`duckdb -readonly data/health_compass.duckdb` opens a SQL prompt, and
`duckdb -readonly -ui data/health_compass.duckdb` opens the same database as a notebook in your
browser at http://localhost:4213. `-readonly` lets the API and a build run at the same time. An
open session keeps reading the file it opened, so reopen it after a build to see the new data.

```sql
-- Every table, with its columns.
show all tables;

-- Alopecia trials, nearest Singapore first.
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

-- Trials that stopped recruiting, latest first.
select t.nct_id, c.changed_on, c.from_status, c.to_status
from marts.fct_trial_status_changes c
join marts.dim_trials t using (trial_key)
order by c.changed_on desc, t.nct_id
limit 10;

-- Every version of the first of those trials (SCD type 2).
select nct_id, valid_from, valid_to, is_current, overall_status, is_recruiting
from marts.dim_trials
where nct_id = (
    select min(t.nct_id)
    from marts.fct_trial_status_changes
    join marts.dim_trials t using (trial_key)
)
order by valid_from;

-- The bridge fans out: a trial with several alopecia conditions is counted once per condition.
select count(*) as condition_links, count(distinct trial_key) as trials
from marts.bridge_trial_conditions
join marts.dim_conditions using (condition_key)
where condition_name ilike '%alopecia%';
```

### Find trials

Once the trial tables are built (see below), `POST /trials` finds trials for a condition, each at
its nearest recruiting site (or its nearest site, if none is recruiting; `site_status` says
which). Recruiting trials come first, nearest first, then trials that have stopped recruiting
(`is_recruiting: false`, with their `overall_status`). The search is a POST
body, so the condition and location stay out of URLs, logs and traces.

```bash
curl -s localhost:8000/trials -H 'content-type: application/json' \
  -d '{"condition": "alopecia", "latitude": 1.29, "longitude": 103.85, "limit": 3}'
```

The query is plain SQL in `src/health_compass/find_trials.sql`. The distance comes from the
haversine formula. Each request opens its own read-only DuckDB connection, so it reads the latest
build.

### Ask the agent

`POST /ask` answers a question about trials near you through a LangGraph agent
(`src/health_compass/agent.py`). Put a free Gemini key from https://aistudio.google.com/apikey in
`.env` as `GOOGLE_API_KEY`.

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "Any hair loss trials near me?", "latitude": 1.29, "longitude": 103.85}'
```

- **`check_input` (gateway):** the LLM sorts the question. Dosing or "should I take X" questions,
  and anything that isn't a trial search, get a fixed refusal (`"outcome": "refused"`).
- **`find_trials`:** the same SQL as `/trials`, for the condition the LLM named ("hair loss"
  becomes "alopecia").
- **`write_answer`:** the LLM writes a summary and one note per trial. It sees each trial's ID,
  title and status, never the location.
- **`check_output` (gateway):** every note must cite a trial that was found, with no doses or
  advice. Then a judge LLM (`judge.py`) reads each note next to the trial's title and summary,
  and rejects any note that claims something the record doesn't say. A failed draft is
  rewritten once with the reasons; if it fails again, the trials are shown without notes
  (`"outcome": "trials_only"`). Set `JUDGE_MODEL` to use a different LLM for the judge.

Status, site and distance always come from the tables, not the LLM. To use another model, set
`LLM_MODEL` in `.env` (e.g. `anthropic:claude-haiku-4-5`) and `uv add` its LangChain package
(`langchain-anthropic`). Each node is a span in Jaeger; the question and location are never logged.
The free Gemini tier may use questions to improve Google's products.

### Run the evals (the release gate)

```bash
uv run --env-file .env python -m health_compass.evals
```

This asks the agent the 15 questions in `evals/cases.yaml`, from Singapore, about 30 real trials
saved in `evals/trials.json.gz`, so every run searches the same data. It takes about 2 minutes,
prints each case, writes `data/eval_results.json`, and fails if a score is below its threshold:
- **`outcome_accuracy`:** the share of questions with the expected outcome (answered, refused or
  no trials).
- **`refusal_recall`:** the share of dosing, personal-advice and off-topic questions that are
  refused. It must be 100%.
- **`groundedness`:** the same judge as `check_output` checks every note again, so the score
  drops if the runtime check is ever removed or weakened.

The `evals` CI job runs this on every PR, with the `GOOGLE_API_KEY` GitHub secret, and uploads the results file.

### Load trials

```bash
uv run --env-file .env python -m health_compass.ingest.clinicaltrials   # first run: all recruiting trials
uv run --env-file .env python -m health_compass.ingest.clinicaltrials --max-pages 2   # quick test of the download
```

A `--max-pages` run stops early, so it is marked incomplete and the transform skips it: run the
full load once before building the tables.

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
(one row per trial per load, the JSON unchanged), one page at a time. dbt only transforms, reading
those tables as sources, in three layers. DuckDB may use at most `DUCKDB_MEMORY_LIMIT` (default
`1GB`), so a full build runs on a small laptop: about 1.2 GB for the whole process.

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
tables and staging keep their rows from earlier builds, the new file starts as a copy of the live
one. See "Explore the data" above for queries.

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
