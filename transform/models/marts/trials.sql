select
    nct_id,
    title,
    summary,
    overall_status,
    study_type,
    phases,
    lead_sponsor,
    lead_sponsor_class,
    enrollment,
    start_date,
    last_updated,
    sex,
    minimum_age,
    maximum_age,
    healthy_volunteers,
    eligibility_criteria
from {{ ref('stg_clinicaltrials__studies') }}
