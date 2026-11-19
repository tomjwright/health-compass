-- One row per site a trial lists. Some trials list a site twice, with different statuses.
select
    versions.trial_key,
    versions.lead_sponsor,
    versions.lead_sponsor_class,
    sites.facility,
    sites.city,
    sites.state,
    sites.country,
    sites.latitude,
    sites.longitude,
    sites.site_status
from {{ ref('int_trial_versions') }} as versions
inner join {{ ref('stg_clinicaltrials__locations') }} as sites using (study_id)
where versions.is_current
