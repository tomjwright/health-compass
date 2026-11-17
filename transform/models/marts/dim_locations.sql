{% set location = ['facility', 'city', 'state', 'country', 'latitude', 'longitude'] %}
select distinct
    {{ dbt_utils.generate_surrogate_key(location) }} as location_key,
    {{ location | join(', ') }}
from {{ ref('int_trial_sites') }}
