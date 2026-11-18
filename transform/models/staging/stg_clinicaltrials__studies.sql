{{ config(materialized='incremental', incremental_strategy='append', on_schema_change='append_new_columns') }}

-- Append-only: one row per trial per load. Each build adds only the loads not in this table yet.
-- `from_json` parses each study once, keeping only the fields below. Extracting each field with
-- `->>` instead parses the whole study again for every field, and needs over 2 GB on a full load.
with studies as (
    select
        load_id,
        from_json(study, '{
            "protocolSection": {
                "identificationModule": {"nctId": "VARCHAR", "briefTitle": "VARCHAR"},
                "descriptionModule": {"briefSummary": "VARCHAR"},
                "statusModule": {
                    "overallStatus": "VARCHAR",
                    "startDateStruct": {"date": "VARCHAR"},
                    "lastUpdatePostDateStruct": {"date": "DATE"}
                },
                "designModule": {
                    "studyType": "VARCHAR",
                    "phases": "VARCHAR[]",
                    "enrollmentInfo": {"count": "INTEGER"}
                },
                "sponsorCollaboratorsModule": {"leadSponsor": {"name": "VARCHAR", "class": "VARCHAR"}},
                "eligibilityModule": {
                    "sex": "VARCHAR",
                    "minimumAge": "VARCHAR",
                    "maximumAge": "VARCHAR",
                    "healthyVolunteers": "BOOLEAN",
                    "eligibilityCriteria": "VARCHAR"
                },
                "conditionsModule": {"conditions": "VARCHAR[]"},
                "armsInterventionsModule": {"interventions": "JSON[]"},
                "contactsLocationsModule": {"locations": "JSON[]"}
            },
            "derivedSection": {"conditionBrowseModule": {"meshes": "JSON[]"}}
        }') as study
    from {{ source('raw', 'clinicaltrials_studies') }}
    {% if is_incremental() %}
    where load_id not in (select load_id from {{ this }})
    {% endif %}
),

sections as (
    select load_id, study.protocolSection as p, study.derivedSection as d
    from studies
)

select
    load_id,
    loads.data_timestamp,
    p.identificationModule.nctId as nct_id,
    p.identificationModule.briefTitle as title,
    p.descriptionModule.briefSummary as summary,
    p.statusModule.overallStatus as overall_status,
    p.designModule.studyType as study_type,
    p.designModule.phases as phases,
    p.sponsorCollaboratorsModule.leadSponsor.name as lead_sponsor,
    p.sponsorCollaboratorsModule.leadSponsor.class as lead_sponsor_class,
    p.designModule.enrollmentInfo.count as enrollment,
    p.statusModule.startDateStruct.date as start_date,
    p.statusModule.lastUpdatePostDateStruct.date as last_updated,
    p.eligibilityModule.sex as sex,
    p.eligibilityModule.minimumAge as minimum_age,
    p.eligibilityModule.maximumAge as maximum_age,
    p.eligibilityModule.healthyVolunteers as healthy_volunteers,
    p.eligibilityModule.eligibilityCriteria as eligibility_criteria,
    p.conditionsModule.conditions as conditions,
    d.conditionBrowseModule.meshes as condition_meshes,
    p.armsInterventionsModule.interventions as interventions,
    p.contactsLocationsModule.locations as locations
from sections
inner join {{ ref('stg_clinicaltrials__loads') }} as loads using (load_id)
