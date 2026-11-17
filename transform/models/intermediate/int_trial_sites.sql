-- One row per site a trial lists. Some trials list a site twice, with different statuses.
select
    nct_id,
    lead_sponsor,
    lead_sponsor_class,
    site ->> 'facility' as facility,
    site ->> 'city' as city,
    site ->> 'state' as state,
    site ->> 'country' as country,
    cast(site ->> '$.geoPoint.lat' as double) as latitude,
    cast(site ->> '$.geoPoint.lon' as double) as longitude,
    site ->> 'status' as site_status
from {{ ref('stg_clinicaltrials__studies') }}, unnest(locations) as t (site)
