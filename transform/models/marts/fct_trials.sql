select
    trial_key,
    {{ dbt_utils.generate_surrogate_key(['lead_sponsor', 'lead_sponsor_class']) }} as sponsor_key,
    last_updated as last_updated_date,
    enrollment
from {{ ref('int_trial_versions') }}
where is_current
