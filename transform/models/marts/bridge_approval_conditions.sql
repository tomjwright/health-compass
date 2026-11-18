-- One row per EU approval per MeSH heading it's for. US approvals have no MeSH headings: search
-- fct_approvals.indications (the label's text) for those.
select distinct
    {{ dbt_utils.generate_surrogate_key(["'EU'", 'ema_product_number', 'medicine_name']) }} as approval_key,
    trim(area) as condition_name
from {{ ref('stg_ema__medicines') }}, unnest(string_split(therapeutic_areas, ';')) as t (area)
where trim(area) != ''
