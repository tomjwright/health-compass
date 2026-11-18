{{ config(materialized='view') }}

-- One row per FDA drug label per application it names.
select
    label ->> 'set_id' as set_id,
    -- A few dates carry extra digits after YYYYMMDD.
    cast(try_strptime(left(label ->> 'effective_time', 8), '%Y%m%d') as date) as effective_on,
    unnest(from_json(label -> 'openfda' -> 'application_number', '["VARCHAR"]')) as application_number,
    -- Upper case, as Drugs@FDA writes brand names.
    from_json(upper(label -> 'openfda' ->> 'brand_name'), '["VARCHAR"]') as brand_names,
    array_to_string(from_json(label -> 'indications_and_usage', '["VARCHAR"]'), ' ') as indications
from {{ source('raw', 'fda_labels') }}
