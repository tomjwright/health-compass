{{ config(materialized='view') }}

-- One row per medicine for people that EMA has assessed, in any status.
select
    medicine ->> 'ema_product_number' as ema_product_number,
    medicine ->> 'name_of_medicine' as medicine_name,
    lower(medicine ->> 'active_substance') as active_substance,
    medicine ->> 'marketing_authorisation_developer_applicant_holder' as holder,
    medicine ->> 'medicine_status' as medicine_status,
    cast(try_strptime(medicine ->> 'marketing_authorisation_date', '%d/%m/%Y') as date) as authorised_on,
    medicine ->> 'conditional_approval' = 'Yes' as is_conditional,
    medicine ->> 'orphan_medicine' = 'Yes' as is_orphan,
    -- MeSH headings, separated by ";".
    nullif(medicine ->> 'therapeutic_area_mesh', '') as therapeutic_areas,
    medicine ->> 'therapeutic_indication' as indications,
    medicine ->> 'medicine_url' as source_url
from {{ source('raw', 'ema_medicines') }}
