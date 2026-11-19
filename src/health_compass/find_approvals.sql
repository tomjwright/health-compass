-- Medicines approved now for a condition, newest approval first.
-- EU: medicines EMA lists under the condition's MeSH heading.
-- US: brand-name approvals (NDA, BLA; not generics, which copy a brand's label) whose label's
-- indications mention any of the search terms. US labels have no MeSH headings.
with matching as (
    select approval_key
    from marts.bridge_approval_conditions
    where condition_name ilike '%' || $condition || '%'

    union

    select approval_key
    from marts.fct_approvals, unnest($terms) as t (term)
    where region = 'US'
        and (approval_id like 'NDA%' or approval_id like 'BLA%')
        and indications ilike '%' || term || '%'
)

select
    region,
    approval_id,
    medicine_name,
    active_substance,
    holder,
    status,
    approved_on,
    is_conditional,
    is_orphan,
    indications,
    source_url
from marts.fct_approvals
join matching using (approval_key)
where status in ('Authorised', 'Marketed')
order by approved_on desc nulls last, medicine_name
limit $limit
