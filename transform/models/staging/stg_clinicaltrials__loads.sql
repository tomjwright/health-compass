select
    _dlt_load_id as load_id,
    cast(data_timestamp as timestamp) as data_timestamp,
    mode,
    cast(since as date) as since
from {{ source('clinicaltrials', 'loads') }}
