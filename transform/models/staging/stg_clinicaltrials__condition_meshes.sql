-- The trial's conditions as MeSH headings, which ClinicalTrials.gov derives.
select _dlt_parent_id as study_id, id as mesh_id, term as condition_name
from {{ source('clinicaltrials', 'studies__derived_section__condition_browse_module__meshes') }}
