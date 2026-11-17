-- Every day from when ClinicalTrials.gov opened (2000) to well past today.
select
    cast(day as date) as date_day,
    year(day) as year,
    quarter(day) as quarter,
    month(day) as month,
    monthname(day) as month_name,
    isodow(day) as day_of_week,
    dayname(day) as day_name
from range(date '1999-01-01', date '2041-01-01', interval 1 day) as t (day)
