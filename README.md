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
openFDA, EMA ───────┼─ dlt ─► raw.duckdb ─► dbt (DuckDB) ─► marts ───────────┐
Europe PMC ─────────┘                                                        │
                                                                             ▼
Next.js ◄── FastAPI ◄── LangGraph supervisor ─► trials / evidence / approvals agents
                          │  input check ─► specialists ─► verifier ─► output check
                          └─ OpenTelemetry traces + JSON logs ─► Jaeger / Langfuse
```

- **Data:** dlt loads each source into `raw.duckdb`, unnested into tables, with a schema
  contract: new columns are added, a column that changes type stops the load. dbt models it. The finished DuckDB file is swapped in whole, and the API
  and agents open it read-only.
- **Durable execution:** agent runs are DBOS workflows on Postgres, so a crashed run resumes
  from its last finished step.
- **Agents:** a LangGraph supervisor sends work to specialists. Every answer passes an input
  check, a verifier (each claim must match a cited source) and an output check.
- **Release gate:** an eval suite of reference questions runs in CI and blocks a merge when
  scores drop; every live answer is also scored before it is shown.
- **Privacy:** no accounts, no saved health details, and personal details are removed from traces.

## Run it on your machine

Needs [uv](https://docs.astral.sh/uv/) and Docker, on Windows, macOS or Linux. The commands below
work the same in PowerShell, bash and zsh.

```bash
uv sync
cp .env.example .env
docker compose up -d                      # Postgres (DBOS) + Jaeger (traces)
uv run --env-file .env python -m health_compass.ingest.clinicaltrials   # all recruiting trials: ~3 min
uv run --env-file .env python -m health_compass.ingest.openfda          # US drug approvals and labels: ~3 min
uv run --env-file .env python -m health_compass.ingest.ema              # EU medicines: a few seconds
uv run --env-file .env python -m health_compass.transform               # dbt build: ~1 min, ~1 GB file
uv run --env-file .env uvicorn health_compass.api:app --reload          # the API on port 8000
curl localhost:8000/health                # {"status":"ok"}
```

Run the ingestion and the transform again on a later day to load only the trials updated since:
about 1,000 trials in a few seconds. Trial history and status changes appear from this second load.

Try the API from your browser at http://localhost:8000/docs. Traces show up in Jaeger at
http://localhost:16686 (service `health-compass-api`).

### Explore the data

The easiest way is a database app such as DBeaver Community (free, from
https://dbeaver.io/download): New Database Connection → DuckDB, choose
`data/health_compass.duckdb`, and add the driver property `duckdb.read_only` = `true`. Leave
DBeaver's own "Read-only connection" box unticked: DuckDB's driver can't switch to read-only after
connecting, so DBeaver fails with "Can't change read-only status on connection level". On Windows,
close it before running the transform, which can't replace a file another program has open.


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

With the API running and the trial tables built, install [Node.js 22+](https://nodejs.org/)
and run this from the repo root:

```bash
npm --prefix web ci
npm --prefix web run dev
```

Open http://localhost:3000 and enter a condition. Choose a country to narrow city suggestions
as you type, then select a city and search. You can also use your current location.
The map shows the site listed for each trial. City lookup uses Open-Meteo (GeoNames) and the
map uses OpenStreetMap tiles. For a FastAPI server on a different address, set
`HEALTH_COMPASS_API_URL` in `web/.env.local` before starting Next.js.

Select **Ask** to enter a trial question, follow the agent's progress, and open its ClinicalTrials.gov
sources. The API needs an LLM key for this page. See [Ask the agent](#ask-the-agent) for setup.

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

### Find approved medicines

Once the approval tables are built, `POST /approvals` lists the medicines approved now for a
condition: EU medicines that EMA lists as "Authorised" under the condition's MeSH heading, and US
brand-name medicines (NDA and BLA applications) that are "Marketed" and whose FDA label's
indications mention the condition or one of up to three other `terms` for it. Generics (ANDA) are
left out: they copy a brand's label. Newest approval first.

```bash
curl -s localhost:8000/approvals -H 'content-type: application/json' \
  -d '{"condition": "Alopecia Areata", "limit": 3}'
```

This returns Leqselvi (US, 2024), Litfulo (EU, 2023) and Litfulo (US, 2023), each with its
holder, approval date, indications and a link to its FDA or EMA page. The query is plain SQL in
`src/health_compass/find_approvals.sql`. US labels have no MeSH headings, so a word like
"psoriasis" also finds older medicines whose label lists it among many conditions.

### Ask the agent

`POST /ask` answers a question about trials near you through a LangGraph agent
(`src/health_compass/agent.py`). Put a free Gemini key from https://aistudio.google.com/apikey in
`.env` as `GOOGLE_API_KEY`. The API needs Postgres (`docker compose up -d`) for durable runs.

```bash
curl -s localhost:8000/ask -H 'content-type: application/json' \
  -d '{"question": "Any hair loss trials near me?", "latitude": 1.29, "longitude": 103.85}'
# {"run_id": "5f0c...", "status": "running", "steps_done": [], "answer": null}
curl -s localhost:8000/ask/5f0c...   # "running" with steps_done so far, then "done" with the answer
```

Each run is a durable DBOS workflow, and each step below is a DBOS step saved in Postgres. If the
API stops mid-run, it resumes the run from its last finished step when it starts again. The run,
including the question, location and answer, is deleted after `RUN_TTL_MINUTES` (60 by default).

- **`check_input` (gateway):** the LLM sorts the question. Dosing or "should I take X" questions,
  and anything that isn't a trial search, get a fixed refusal (`"outcome": "refused"`).
- **`find_trials`:** the same SQL as `/trials`, for the condition's official MeSH heading, which
  the LLM names ("hair loss" becomes "Alopecia", "T2DM" becomes "Diabetes Mellitus, Type 2").
  Trials store MeSH headings next to the sponsor's own wording, so this finds trials however
  the user put it.
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

This asks the agent the 16 questions in `evals/cases.yaml`, from Singapore, about 30 real trials
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

`src/health_compass/ingest/clinicaltrials.py` loads the trials with dlt into the
`clinicaltrials` schema of `data/raw.duckdb`. Each load is appended, with its own `_dlt_load_id`.

- **First run (`mode` = `full`):** every recruiting trial, about 65,000.
- **Every later run (`mode` = `incremental`):** only the trials updated since the day before the
  last load (`since`), in any status, so we see trials that stop recruiting. That's about 1,000
  trials a day. The day of overlap means a late update is never missed; dbt drops the duplicates.
- **Quick test:** a `--max-pages` run loads only those pages and doesn't save the version, so the
  next run loads every recruiting trial again.
- **Nothing new:** dlt saves the version of the data it loaded (`dataTimestamp` from the API's
  `/version`). A run that finds the same version again does nothing.
- **Only what we use:** `keep_used_fields` keeps the modules the dbt models read (identification,
  status, design, eligibility, conditions, the lead sponsor, interventions, sites and MeSH
  headings) and drops the rest (outcomes, references, results, ...), which would otherwise be
  dozens of child tables. It also drops each site's contacts: people's names, phones and emails. dlt unnests the lists that are left into
  `clinicaltrials.studies__protocol_section__conditions_module__conditions` and so on.
- **Schema evolution:** the same `SCHEMA_CONTRACT` as below. `STUDY_COLUMNS` types the columns
  dbt reads, so they exist from the first load and a type change stops the load. A field the API
  removes isn't noticed by dlt; it becomes null, and the dbt tests on the marts catch it.
- **Memory:** dlt writes `jsonl` load files (its default for DuckDB used 1.6 GB on 5 pages). A
  full load takes about 3 minutes and peaks at about 1 GB; `raw.duckdb` is then about 800 MB.

### Load drug approvals

```bash
uv run --env-file .env python -m health_compass.ingest.openfda
uv run --env-file .env python -m health_compass.ingest.ema
```

Each source has its own module in `src/health_compass/ingest/`, which loads it with
[dlt](https://dlthub.com/docs) into its own schema in `data/raw.duckdb` (`RAW_DUCKDB_PATH`).
What they share (HTTP retries, the dlt pipeline, the schema contract) is in `common.py`.

| Module | Tables | Source | Records |
|---|---|---|---|
| `openfda.py` | `openfda.applications` and its child tables | [openFDA Drugs@FDA](https://open.fda.gov/apis/drug/drugsfda/): every US drug application, its products and approval history | ~29,500 |
| `openfda.py` | `openfda.labels` and its child tables | [openFDA drug labels](https://open.fda.gov/apis/drug/label/): what each label says the drug is for (`indications_and_usage`) | ~75,000 |
| `ema.py` | `ema.medicines` | [EMA medicines report](https://www.ema.europa.eu/en/medicines/download-medicine-data): every medicine for people EMA has assessed | ~2,400 |

- **Unnesting:** dlt turns each nested list into a child table linked to its parent row by
  `_dlt_parent_id` = `_dlt_id`, e.g. `openfda.applications__products` and
  `openfda.applications__products__active_ingredients`, 13 tables in all. An application's
  `openfda` block (a dozen lists of codes we don't use) is kept as one JSON column instead.
- **Schema evolution:** dlt keeps each table's schema. New tables and columns are added; a column
  that changes type stops the load (`SCHEMA_CONTRACT` in `common.py`). A column that only ever
  holds nulls is not created, so dbt fails rather than reading a column that isn't there.
- **Full copies:** each load is a full copy of every approval, so it replaces the tables. dlt
  loads into `openfda_staging` first and swaps the tables in within one transaction, so a run
  that stops halfway leaves the previous load in place.
- **Memory:** openFDA's labels are 14 zip files of about 130 MB each. Each is streamed record by
  record, and only labels that name an FDA application are kept, with a few fields. dlt then
  loads small files one at a time (`.dlt/config.toml`), so the run peaks at about 520 MB.

### Build the trial tables

```bash
uv run --env-file .env python -m health_compass.transform
```

dbt attaches `data/raw.duckdb` read-only and builds `data/health_compass.duckdb` from it, in
three layers. Until openFDA and EMA are both loaded, the build skips the approvals tables. DuckDB
may use at most `DUCKDB_MEMORY_LIMIT` (default `1GB`), so a full build runs on a small laptop.

- `staging`: `stg_clinicaltrials__studies` is append-only, one row per trial per load, renamed and
  typed. Each build adds only the loads it hasn't seen yet. Its lists (conditions, interventions,
  sites, MeSH headings) have a staging table each, joined on `study_id`. They are tables, not
  views: a view would point at `raw.duckdb`, which the API doesn't attach.
- `intermediate`: `int_trial_versions` keeps one row per version of a trial, then views unnest
  the current version's conditions, interventions and sites.
- The approvals' staging models are views: each load replaces the last, so there is nothing to
  append.
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
| `fct_approvals` | drug approval: a brand in an FDA application (`region = 'US'`) or EMA medicine (`'EU'`), with its status, approval date and indications |
| `bridge_approval_conditions` | EU approval per MeSH heading it's approved for |

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
2. ClinicalTrials.gov ingestion (dlt, incremental, schema contract)
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
