-- One row per trial per condition, from both vocabularies.
select distinct
    nct_id,
    'reported' as vocabulary,
    condition as condition_name,
    cast(null as varchar) as mesh_id
from {{ ref('stg_clinicaltrials__studies') }}, unnest(conditions) as t (condition)
union all
select distinct
    nct_id,
    'mesh' as vocabulary,
    mesh ->> 'term' as condition_name,
    mesh ->> 'id' as mesh_id
from {{ ref('stg_clinicaltrials__studies') }}, unnest(condition_meshes) as t (mesh)
