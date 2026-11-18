{{ config(materialized='view') }}

-- One row per brand in an FDA drug application. An application can cover several brands with
-- different ingredients (NAQUA, NAQUIVAL) or statuses (DARANIDE discontinued, KEVEYIS marketed),
-- so each brand keeps its own. Strengths are left out: answers never mention doses.
with applications as (
    select
        application ->> 'application_number' as application_number,
        application ->> 'sponsor_name' as sponsor_name,
        cast(application -> 'products' as json[]) as products,
        cast(application -> 'submissions' as json[]) as submissions
    from {{ source('raw', 'fda_applications') }}
),

products as (
    select application_number, unnest(products) as product
    from applications
),

brands as (
    select
        application_number,
        product ->> 'brand_name' as brand_name,
        list(distinct product ->> 'marketing_status' order by product ->> 'marketing_status') as marketing_statuses
    from products
    group by application_number, brand_name
),

ingredients as (
    select
        application_number,
        product ->> 'brand_name' as brand_name,
        string_agg(distinct lower(ingredient ->> 'name'), ' / ' order by lower(ingredient ->> 'name')) as active_substance
    from products, unnest(cast(product -> 'active_ingredients' as json[])) as t (ingredient)
    group by application_number, brand_name
),

-- The original application's approval ("ORIG", status "AP"), not later supplements.
-- A few dates carry extra digits after YYYYMMDD.
original_approvals as (
    select
        application_number,
        cast(min(try_strptime(left(submission ->> 'submission_status_date', 8), '%Y%m%d')) as date) as approved_on
    from applications, unnest(submissions) as t (submission)
    where submission ->> 'submission_type' = 'ORIG' and submission ->> 'submission_status' = 'AP'
    group by application_number
)

select application_number, sponsor_name, brand_name, active_substance, marketing_statuses, approved_on
from brands
join applications using (application_number)
left join ingredients using (application_number, brand_name)
left join original_approvals using (application_number)
