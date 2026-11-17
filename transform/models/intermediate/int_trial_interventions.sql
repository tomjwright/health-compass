-- One row per trial per intervention (a trial can list the same one twice, e.g. in two arms).
select distinct
    nct_id,
    intervention ->> 'type' as intervention_type,
    intervention ->> 'name' as intervention_name
from {{ ref('stg_clinicaltrials__studies') }}, unnest(interventions) as t (intervention)
