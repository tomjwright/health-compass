-- SCD type 2: one row per version of a trial. Filter on is_current for each trial as it is now.
select
    trial_key,
    nct_id,
    title,
    summary,
    overall_status,
    overall_status = 'RECRUITING' as is_recruiting,
    study_type,
    phases,
    start_date,
    sex,
    minimum_age,
    maximum_age,
    healthy_volunteers,
    eligibility_criteria,
    valid_from,
    valid_to,
    is_current
from {{ ref('int_trial_versions') }}
