-- One row per trial per condition, from both vocabularies.
select distinct
    versions.trial_key,
    'reported' as vocabulary,
    conditions.condition_name,
    cast(null as varchar) as mesh_id
from {{ ref('int_trial_versions') }} as versions
inner join {{ ref('stg_clinicaltrials__conditions') }} as conditions using (study_id)
where versions.is_current
union all
select distinct
    versions.trial_key,
    'mesh' as vocabulary,
    meshes.condition_name,
    meshes.mesh_id
from {{ ref('int_trial_versions') }} as versions
inner join {{ ref('stg_clinicaltrials__condition_meshes') }} as meshes using (study_id)
where versions.is_current
