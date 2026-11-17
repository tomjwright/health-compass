select
    {{ dbt_utils.generate_surrogate_key(['vocabulary', 'condition_name']) }} as condition_key,
    condition_name,
    vocabulary,
    any_value(mesh_id) as mesh_id
from {{ ref('int_trial_conditions') }}
group by all
