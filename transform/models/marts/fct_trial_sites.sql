{% set location = ['facility', 'city', 'state', 'country', 'latitude', 'longitude'] %}
select
    trial_key,
    {{ dbt_utils.generate_surrogate_key(location) }} as location_key,
    {{ dbt_utils.generate_surrogate_key(['lead_sponsor', 'lead_sponsor_class']) }} as sponsor_key,
    -- A site listed twice counts as recruiting if either listing is.
    arg_min(site_status, site_status is distinct from 'RECRUITING') as site_status
from {{ ref('int_trial_sites') }}
group by all
