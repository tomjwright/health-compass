select
    nct_id,
    intervention ->> 'type' as intervention_type,
    intervention ->> 'name' as intervention_name
from {{ ref('stg_clinicaltrials__studies') }}, unnest(interventions) as t (intervention)
