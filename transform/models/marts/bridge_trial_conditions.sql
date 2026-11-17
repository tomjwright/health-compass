select distinct
    trial_key,
    {{ dbt_utils.generate_surrogate_key(['vocabulary', 'condition_name']) }} as condition_key
from {{ ref('int_trial_conditions') }}
