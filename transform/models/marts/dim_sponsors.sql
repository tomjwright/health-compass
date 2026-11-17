select distinct
    {{ dbt_utils.generate_surrogate_key(['lead_sponsor', 'lead_sponsor_class']) }} as sponsor_key,
    lead_sponsor as sponsor_name,
    lead_sponsor_class as sponsor_class
from {{ ref('int_trial_versions') }}
where is_current
