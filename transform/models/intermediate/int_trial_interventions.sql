-- One row per trial per intervention (a trial can list the same one twice, e.g. in two arms).
select distinct
    trial_key,
    intervention ->> 'type' as intervention_type,
    intervention ->> 'name' as intervention_name
from {{ ref('int_trial_versions') }}, unnest(interventions) as t (intervention)
where is_current
