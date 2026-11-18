-- One row per approval: a brand in an FDA application (US) or an EMA medicine (EU).
with us_products as (
    select *, count(*) over (partition by application_number) as brands_in_application
    from {{ ref('stg_fda__products') }}
),

us_labelled as (
    -- A label belongs to a brand if it names the brand, or if its application has only one brand.
    -- Several labels can match; the newest says what the brand is for now.
    select us_products.*, labels.indications
    from us_products
    left join {{ ref('stg_fda__labels') }} as labels
        on labels.application_number = us_products.application_number
        and (us_products.brands_in_application = 1 or list_contains(labels.brand_names, us_products.brand_name))
    qualify row_number() over (
        partition by us_products.application_number, us_products.brand_name
        order by labels.effective_on desc nulls last, labels.set_id
    ) = 1
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
    from us_labelled
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

select {{ dbt_utils.generate_surrogate_key(['region', 'approval_id', 'medicine_name']) }} as approval_key, *
from (
    select * from us
    union all
    select * from eu
)
