select
    _dlt_parent_id as study_id,
    facility,
    city,
    state,
    country,
    cast(geo_point__lat as double) as latitude,
    cast(geo_point__lon as double) as longitude,
    status as site_status
from {{ source('clinicaltrials', 'studies__protocol_section__contacts_locations_module__locations') }}
