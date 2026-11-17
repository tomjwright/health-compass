select distinct
    {{ dbt_utils.generate_surrogate_key(['nct_id']) }} as trial_key,
    {{ dbt_utils.generate_surrogate_key(['intervention_type', 'intervention_name']) }}
        as intervention_key
from {{ ref('int_trial_interventions') }}
