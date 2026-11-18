{{ config(materialized='view') }}

-- One row per brand in an FDA drug application. An application can cover several brands with
-- different ingredients (NAQUA, NAQUIVAL) or statuses (DARANIDE discontinued, KEVEYIS marketed),
-- so each brand keeps its own. Strengths are left out: answers never mention doses.
-- dlt links each child table to its parent row: child._dlt_parent_id = parent._dlt_id.
with products as (
    select
        applications.application_number,
        applications.sponsor_name,
        products._dlt_id as product_id,
        products.brand_name,
        products.marketing_status
    from {{ source('openfda', 'applications') }} as applications
    join {{ source('openfda', 'applications__products') }} as products
        on products._dlt_parent_id = applications._dlt_id
),

brands as (
    select
        application_number,
        sponsor_name,
        brand_name,
        list(distinct marketing_status order by marketing_status) as marketing_statuses
    from products
    group by application_number, sponsor_name, brand_name
),

ingredients as (
    select
        products.application_number,
        products.brand_name,
        string_agg(distinct lower(ingredients.name), ' / ' order by lower(ingredients.name)) as active_substance
    from products
    join {{ source('openfda', 'applications__products__active_ingredients') }} as ingredients
        on ingredients._dlt_parent_id = products.product_id
    group by products.application_number, products.brand_name
),

-- The original application's approval ("ORIG", status "AP"), not later supplements.
-- A few dates carry extra digits after YYYYMMDD.
original_approvals as (
    select
        applications.application_number,
        cast(min(try_strptime(left(submissions.submission_status_date, 8), '%Y%m%d')) as date) as approved_on
    from {{ source('openfda', 'applications') }} as applications
    join {{ source('openfda', 'applications__submissions') }} as submissions
        on submissions._dlt_parent_id = applications._dlt_id
    where submissions.submission_type = 'ORIG' and submissions.submission_status = 'AP'
    group by applications.application_number
)

select application_number, sponsor_name, brand_name, active_substance, marketing_statuses, approved_on
from brands
left join ingredients using (application_number, brand_name)
left join original_approvals using (application_number)
