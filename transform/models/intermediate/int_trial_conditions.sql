-- One row per trial per condition, from both vocabularies.
select distinct
    trial_key,
    'reported' as vocabulary,
    condition as condition_name,
    cast(null as varchar) as mesh_id
from {{ ref('int_trial_versions') }}, unnest(conditions) as t (condition)
where is_current
union all
select distinct
    trial_key,
    'mesh' as vocabulary,
    mesh ->> 'term' as condition_name,
    mesh ->> 'id' as mesh_id
from {{ ref('int_trial_versions') }}, unnest(condition_meshes) as t (mesh)
where is_current
