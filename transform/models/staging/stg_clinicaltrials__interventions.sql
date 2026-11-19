select _dlt_parent_id as study_id, type as intervention_type, name as intervention_name
from {{ source('clinicaltrials', 'studies__protocol_section__arms_interventions_module__interventions') }}
