-- Conditions as the sponsor wrote them ("reported"), plus the MeSH terms ClinicalTrials.gov
-- matched them to ("mesh"), so "hair loss" can find trials filed under "Alopecia".
select distinct nct_id, unnest(conditions) as condition, 'reported' as source
from {{ ref('stg_clinicaltrials__studies') }}
union all
select distinct nct_id, unnest(mesh_terms) as condition, 'mesh' as source
from {{ ref('stg_clinicaltrials__studies') }}
