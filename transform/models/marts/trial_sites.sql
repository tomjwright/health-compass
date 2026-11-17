-- One row per trial site. Sites without a geoPoint have null coordinates.
select
    nct_id,
    site ->> 'facility' as facility,
    site ->> 'city' as city,
    site ->> 'state' as state,
    site ->> 'country' as country,
    site ->> 'status' as site_status,
    cast(site ->> '$.geoPoint.lat' as double) as latitude,
    cast(site ->> '$.geoPoint.lon' as double) as longitude
from {{ ref('stg_clinicaltrials__studies') }}, unnest(locations) as t (site)
