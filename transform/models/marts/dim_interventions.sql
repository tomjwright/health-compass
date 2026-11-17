select distinct
    {{ dbt_utils.generate_surrogate_key(['intervention_type', 'intervention_name']) }}
        as intervention_key,
    intervention_type,
    intervention_name
from {{ ref('int_trial_interventions') }}
