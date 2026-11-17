-- Trials for a condition, each shown at its site nearest the user.
-- Recruiting trials come first, then trials that have stopped recruiting, each nearest first.
with matching_trials as (
    select distinct trial_key
    from marts.bridge_trial_conditions
    join marts.dim_conditions using (condition_key)
    where condition_name ilike '%' || $condition || '%'
),

sites as (
    select
        t.nct_id,
        t.title,
        t.overall_status,
        t.is_recruiting,
        l.facility,
        l.city,
        l.country,
        -- Haversine formula: the distance along the Earth's surface (radius 6,371 km).
        2 * 6371 * asin(sqrt(
            sin(radians(l.latitude - $latitude) / 2) ^ 2
            + cos(radians($latitude)) * cos(radians(l.latitude))
            * sin(radians(l.longitude - $longitude) / 2) ^ 2
        )) as distance_km
    from matching_trials
    join marts.dim_trials as t using (trial_key)
    join marts.fct_trial_sites using (trial_key)
    join marts.dim_locations as l using (location_key)
    where l.latitude is not null
)

select
    nct_id, title, overall_status, is_recruiting, facility, city, country,
    round(distance_km, 1) as distance_km
from sites
qualify row_number() over (partition by nct_id order by distance_km) = 1
order by is_recruiting desc, distance_km, nct_id
limit $limit
