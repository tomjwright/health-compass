{{ config(materialized='view') }}

-- Loads cut short (--max-pages) have "complete": false and are left out.
select
    regexp_extract(filename, '([^/]+)/manifest\.json$', 1) as load_id,
    cast(data_timestamp as timestamp) as data_timestamp,
    coalesce(mode, 'full') as mode,
    cast(since as date) as since
from {{ source('clinicaltrials', 'loads') }}
where complete
