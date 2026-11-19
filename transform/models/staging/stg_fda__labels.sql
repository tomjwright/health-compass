{{ config(materialized='view') }}

-- One row per FDA drug label per application it names.
with indications as (
    select _dlt_parent_id, string_agg(value, ' ' order by _dlt_list_idx) as indications
    from {{ source('openfda', 'labels__indications_and_usage') }}
    group by _dlt_parent_id
),

brands as (
    -- Upper case, as Drugs@FDA writes brand names.
    select _dlt_parent_id, list(upper(value) order by _dlt_list_idx) as brand_names
    from {{ source('openfda', 'labels__openfda__brand_name') }}
    group by _dlt_parent_id
)

select
    labels.set_id,
    -- A few dates carry extra digits after YYYYMMDD.
    cast(try_strptime(left(labels.effective_time, 8), '%Y%m%d') as date) as effective_on,
    applications.value as application_number,
    brands.brand_names,
    indications.indications
from {{ source('openfda', 'labels') }} as labels
join {{ source('openfda', 'labels__openfda__application_number') }} as applications
    on applications._dlt_parent_id = labels._dlt_id
left join brands on brands._dlt_parent_id = labels._dlt_id
left join indications on indications._dlt_parent_id = labels._dlt_id
