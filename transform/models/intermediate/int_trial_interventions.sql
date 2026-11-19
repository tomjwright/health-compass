-- One row per trial per intervention (a trial can list the same one twice, e.g. in two arms).
select distinct
    versions.trial_key,
    interventions.intervention_type,
    interventions.intervention_name
from {{ ref('int_trial_versions') }} as versions
inner join {{ ref('stg_clinicaltrials__interventions') }} as interventions using (study_id)
where versions.is_current
