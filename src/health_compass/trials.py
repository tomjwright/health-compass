"""Trial records in Ask answers and existing durable workflow results."""

from pydantic import BaseModel


class TablesNotBuiltError(Exception):
    """An error recorded by Ask runs started before the live search."""


class Trial(BaseModel):
    """A study and its nearest recruiting site."""

    nct_id: str
    title: str | None
    summary: str | None
    overall_status: str
    is_recruiting: bool
    facility: str | None
    city: str | None
    country: str | None
    site_status: str | None
    distance_km: float


class LocatedTrial(Trial):
    """A trial record stored by Ask runs started before the live search."""

    latitude: float
    longitude: float
