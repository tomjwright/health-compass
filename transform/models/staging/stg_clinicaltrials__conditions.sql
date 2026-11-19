-- The trial's conditions, as the sponsor wrote them.
select _dlt_parent_id as study_id, value as condition_name
from {{ source('clinicaltrials', 'studies__protocol_section__conditions_module__conditions') }}
