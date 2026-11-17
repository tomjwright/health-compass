{{ config(materialized='incremental', incremental_strategy='append', on_schema_change='append_new_columns') }}

-- Append-only: one row per trial per load. Each build parses only the loads not in this table
-- yet; filtering on the folder name in the file path lets DuckDB skip every other load's files.
{% set new_loads_query %}
    select load_id from {{ ref('stg_clinicaltrials__loads') }}
    {% if is_incremental() %} where load_id not in (select load_id from {{ this }}) {% endif %}
{% endset %}
{% set new_loads = run_query(new_loads_query).columns[0].values() if execute else [] %}

with pages as (
    select regexp_extract(filename, '([^/]+)/[^/]+$', 1) as load_id, studies
    from {{ source('clinicaltrials', 'study_pages') }}
    where regexp_extract(filename, '([^/]+)/[^/]+$', 1) in ('{{ new_loads | join("', '") }}')
),

studies as (
    select load_id, unnest(studies) as study
    from pages
),

sections as (
    select load_id, study -> '$.protocolSection' as p, study -> '$.derivedSection' as d
    from studies
)

select
    load_id,
    loads.data_timestamp,
    p ->> '$.identificationModule.nctId' as nct_id,
    p ->> '$.identificationModule.briefTitle' as title,
    p ->> '$.descriptionModule.briefSummary' as summary,
    p ->> '$.statusModule.overallStatus' as overall_status,
    p ->> '$.designModule.studyType' as study_type,
    cast(p -> '$.designModule.phases' as varchar[]) as phases,
    p ->> '$.sponsorCollaboratorsModule.leadSponsor.name' as lead_sponsor,
    p ->> '$.sponsorCollaboratorsModule.leadSponsor.class' as lead_sponsor_class,
    cast(p ->> '$.designModule.enrollmentInfo.count' as integer) as enrollment,
    p ->> '$.statusModule.startDateStruct.date' as start_date,
    cast(p ->> '$.statusModule.lastUpdatePostDateStruct.date' as date) as last_updated,
    p ->> '$.eligibilityModule.sex' as sex,
    p ->> '$.eligibilityModule.minimumAge' as minimum_age,
    p ->> '$.eligibilityModule.maximumAge' as maximum_age,
    cast(p ->> '$.eligibilityModule.healthyVolunteers' as boolean) as healthy_volunteers,
    p ->> '$.eligibilityModule.eligibilityCriteria' as eligibility_criteria,
    cast(p -> '$.conditionsModule.conditions' as varchar[]) as conditions,
    cast(d -> '$.conditionBrowseModule.meshes' as json[]) as condition_meshes,
    cast(p -> '$.armsInterventionsModule.interventions' as json[]) as interventions,
    cast(p -> '$.contactsLocationsModule.locations' as json[]) as locations
from sections
inner join {{ ref('stg_clinicaltrials__loads') }} as loads using (load_id)
