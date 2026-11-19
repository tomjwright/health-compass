{{ config(materialized='incremental', incremental_strategy='append', on_schema_change='append_new_columns') }}

-- Append-only: one row per trial per load. Each build adds only the loads not in this table yet.
-- The lists (conditions, sites, ...) are in their own staging models, joined on study_id.
with studies as (
    select *
    from {{ source('clinicaltrials', 'studies') }}
    {% if is_incremental() %}
    where _dlt_load_id not in (select load_id from {{ this }})
    {% endif %}
),

phases as (
    select _dlt_parent_id as study_id, list(value order by _dlt_list_idx) as phases
    from {{ source('clinicaltrials', 'studies__protocol_section__design_module__phases') }}
    group by _dlt_parent_id
)

select
    studies._dlt_id as study_id,
    studies._dlt_load_id as load_id,
    loads.data_timestamp,
    protocol_section__identification_module__nct_id as nct_id,
    protocol_section__identification_module__brief_title as title,
    protocol_section__description_module__brief_summary as summary,
    protocol_section__status_module__overall_status as overall_status,
    protocol_section__design_module__study_type as study_type,
    phases.phases,
    protocol_section__sponsor_collaborators_module__lead_sponsor__name as lead_sponsor,
    protocol_section__sponsor_collaborators_module__lead_sponsor__class as lead_sponsor_class,
    cast(protocol_section__design_module__enrollment_info__count as integer) as enrollment,
    protocol_section__status_module__start_date_struct__date as start_date,
    cast(protocol_section__status_module__last_update_post_date_struct__date as date) as last_updated,
    protocol_section__eligibility_module__sex as sex,
    protocol_section__eligibility_module__minimum_age as minimum_age,
    protocol_section__eligibility_module__maximum_age as maximum_age,
    protocol_section__eligibility_module__healthy_volunteers as healthy_volunteers,
    protocol_section__eligibility_module__eligibility_criteria as eligibility_criteria
from studies
inner join {{ ref('stg_clinicaltrials__loads') }} as loads on studies._dlt_load_id = loads.load_id
left join phases on studies._dlt_id = phases.study_id
