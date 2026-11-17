-- One row each time a trial's overall status changed between versions, e.g. RECRUITING to COMPLETED.
select
    trial_key,
    valid_from as changed_on,
    previous_status as from_status,
    overall_status as to_status
from {{ ref('int_trial_versions') }}
where previous_status <> overall_status
