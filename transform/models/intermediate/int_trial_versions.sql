{{ config(materialized='table') }}

-- One row per version of a trial: a new version starts when its last_updated date changes.
-- Loads overlap by a day, so a version can arrive twice; the first arrival is kept.
with versions as (
    select *
    from {{ ref('stg_clinicaltrials__studies') }}
    qualify row_number() over (partition by nct_id, last_updated order by data_timestamp, load_id) = 1
),

history as (
    select
        *,
        last_updated as valid_from,
        lead(last_updated) over (partition by nct_id order by last_updated) as valid_to,
        lag(overall_status) over (partition by nct_id order by last_updated) as previous_status
    from versions
    -- Only trials that were recruiting in some load: incremental loads also bring trials we never
    -- saw recruiting (e.g. a completed trial that posted results), which are out of scope.
    qualify bool_or(overall_status = 'RECRUITING') over (partition by nct_id)
)

select
    {{ dbt_utils.generate_surrogate_key(['nct_id', 'valid_from']) }} as trial_key,
    *,
    valid_to is null as is_current
from history
