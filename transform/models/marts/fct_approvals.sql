-- One row per approval: an FDA application (US) or an EMA medicine (EU).
with us_labels as (
    -- Many labels can name one application; the newest says what it's for now.
    select application_number, indications
    from {{ ref('stg_fda__labels') }}
    qualify row_number() over (partition by application_number order by effective_on desc, set_id) = 1
),

us as (
    select
        'US' as region,
        application_number as approval_id,
        brand_name as medicine_name,
        active_substance,
        sponsor_name as holder,
        case
            when list_has_any(marketing_statuses, ['Prescription', 'Over-the-counter']) then 'Marketed'
            when list_contains(marketing_statuses, 'None (Tentative Approval)') then 'Tentative approval'
            else 'Discontinued'
        end as status,
        approved_on,
        cast(null as boolean) as is_conditional,
        cast(null as boolean) as is_orphan,
        indications,
        'https://www.accessdata.fda.gov/scripts/cder/daf/index.cfm?event=overview.process&ApplNo='
        || regexp_extract(application_number, '[0-9]+') as source_url
    from {{ ref('stg_fda__applications') }}
    left join us_labels using (application_number)
),

eu as (
    select
        'EU' as region,
        ema_product_number as approval_id,
        medicine_name,
        active_substance,
        holder,
        medicine_status as status,
        authorised_on as approved_on,
        is_conditional,
        is_orphan,
        indications,
        source_url
    from {{ ref('stg_ema__medicines') }}
)

select {{ dbt_utils.generate_surrogate_key(['region', 'approval_id']) }} as approval_key, *
from (
    select * from us
    union all by name
    select * from eu
)
