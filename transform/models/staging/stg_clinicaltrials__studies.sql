-- One row per trial in a raw ClinicalTrials.gov load, with the fields the marts use pulled out
-- of the JSON. A field missing from a trial comes through as null.
with studies as (
    select unnest(studies) as study
    from read_json(
        '{{ var("raw_run_dir") }}/page-*.json.gz',
        columns = {studies: 'JSON[]'},
        maximum_object_size = 200000000
    )
),

protocol as (
    select
        study -> '$.protocolSection' as p,
        study ->> '$.derivedSection.conditionBrowseModule.meshes[*].term' as mesh_terms
    from studies
)

select
    p ->> '$.identificationModule.nctId' as nct_id,
    p ->> '$.identificationModule.briefTitle' as title,
    p ->> '$.descriptionModule.briefSummary' as summary,
    p ->> '$.statusModule.overallStatus' as overall_status,
    p ->> '$.designModule.studyType' as study_type,
    cast(p -> '$.designModule.phases' as varchar[]) as phases,
    p ->> '$.sponsorCollaboratorsModule.leadSponsor.name' as lead_sponsor,
    p ->> '$.sponsorCollaboratorsModule.leadSponsor.class' as lead_sponsor_class,
    cast(p ->> '$.designModule.enrollmentInfo.count' as integer) as enrollment,
    -- "2014-04" or "2014-04-02": kept as text so a month is not passed off as a day
    p ->> '$.statusModule.startDateStruct.date' as start_date,
    cast(p ->> '$.statusModule.lastUpdatePostDateStruct.date' as date) as last_updated,
    p ->> '$.eligibilityModule.sex' as sex,
    p ->> '$.eligibilityModule.minimumAge' as minimum_age,
    p ->> '$.eligibilityModule.maximumAge' as maximum_age,
    cast(p ->> '$.eligibilityModule.healthyVolunteers' as boolean) as healthy_volunteers,
    p ->> '$.eligibilityModule.eligibilityCriteria' as eligibility_criteria,
    cast(p -> '$.conditionsModule.conditions' as varchar[]) as conditions,
    mesh_terms,
    cast(p -> '$.armsInterventionsModule.interventions' as json[]) as interventions,
    cast(p -> '$.contactsLocationsModule.locations' as json[]) as locations
from protocol
