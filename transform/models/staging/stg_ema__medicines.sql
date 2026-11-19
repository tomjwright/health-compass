{{ config(materialized='view') }}

-- One row per medicine for people that EMA has assessed, in any status.
select
    ema_product_number,
    name_of_medicine as medicine_name,
    lower(active_substance) as active_substance,
    marketing_authorisation_developer_applicant_holder as holder,
    medicine_status,
    cast(try_strptime(marketing_authorisation_date, '%d/%m/%Y') as date) as authorised_on,
    conditional_approval = 'Yes' as is_conditional,
    orphan_medicine = 'Yes' as is_orphan,
    -- MeSH headings, separated by ";".
    nullif(therapeutic_area_mesh, '') as therapeutic_areas,
    therapeutic_indication as indications,
    medicine_url as source_url
from {{ source('ema', 'medicines') }}
