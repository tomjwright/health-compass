{{ config(materialized='view') }}

select load_id, data_timestamp, mode, since
from {{ source('raw', 'clinicaltrials_loads') }}
