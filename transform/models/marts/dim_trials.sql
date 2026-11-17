select
    {{ dbt_utils.generate_surrogate_key(['nct_id']) }} as trial_key,
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
    eligibility_criteria
from {{ ref('stg_clinicaltrials__studies') }}
