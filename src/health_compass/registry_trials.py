"""Search live ClinicalTrials.gov recruiting studies by condition, country and distance."""

import urllib.request
from math import asin, cos, radians, sin, sqrt
from typing import Literal
from urllib.parse import urlencode

from pydantic import AliasPath, BaseModel, Field, model_validator

from health_compass.ingest.clinicaltrials import API

NEARBY_KM = 100


class Coordinates(BaseModel):
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)


class RegistrySearch(BaseModel):
    condition: str = Field(min_length=2, max_length=100)
    country: str | None = Field(default=None, max_length=80, pattern=r"^[\w .,'\u{2019}()&-]+$")
    origin: Coordinates | None = None
    page_token: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_pagination(self) -> "RegistrySearch":
        if self.origin and self.page_token:
            raise ValueError("Nearby searches return all matches in distance order.")
        return self


class GeoPoint(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class Site(BaseModel):
    facility: str | None = None
    city: str | None = None
    country: str | None = None
    status: str | None = None
    point: GeoPoint | None = Field(default=None, validation_alias="geoPoint")


class NearbySite(Coordinates):
    facility: str | None
    city: str | None
    country: str | None
    distance_km: float


class RecruitingStudy(BaseModel):
    nct_id: str = Field(
        validation_alias=AliasPath("protocolSection", "identificationModule", "nctId")
    )
    title: str = Field(
        validation_alias=AliasPath("protocolSection", "identificationModule", "briefTitle")
    )
    overall_status: Literal["RECRUITING"] = Field(
        validation_alias=AliasPath("protocolSection", "statusModule", "overallStatus")
    )
    locations: list[Site] = Field(
        default_factory=list,
        validation_alias=AliasPath("protocolSection", "contactsLocationsModule", "locations"),
        exclude=True,
    )
    nearest_site: NearbySite | None = None


class RegistryPage(BaseModel):
    trials: list[RecruitingStudy] = Field(validation_alias="studies")
    # The registry includes the total on the first page only.
    total_count: int | None = Field(default=None, validation_alias="totalCount")
    next_page_token: str | None = Field(default=None, validation_alias="nextPageToken")


class RegistryResults(BaseModel):
    trials: list[RecruitingStudy]
    total_count: int | None
    next_page_token: str | None
    radius_km: int | None = None


def fetch_page(params: dict[str, str]) -> RegistryPage:
    url = f"{API}/studies?{urlencode(params)}"
    # Keep the outgoing URL out of logs. The HTTPS host is fixed and all parameters are encoded.
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return RegistryPage.model_validate_json(response.read())


def nearest_site(
    study: RecruitingStudy, origin: Coordinates, country: str | None
) -> NearbySite | None:
    best: NearbySite | None = None
    lat = radians(origin.latitude)
    for site in study.locations:
        if site.status != "RECRUITING" or not site.point or (country and site.country != country):
            continue
        site_lat = radians(site.point.lat)
        delta_lon = radians(site.point.lon - origin.longitude)
        haversine = (
            sin((site_lat - lat) / 2) ** 2 + cos(lat) * cos(site_lat) * sin(delta_lon / 2) ** 2
        )
        distance = 2 * 6371 * asin(sqrt(min(1, haversine)))
        if distance <= NEARBY_KM and (best is None or distance < best.distance_km):
            best = NearbySite(
                facility=site.facility,
                city=site.city,
                country=site.country,
                latitude=site.point.lat,
                longitude=site.point.lon,
                distance_km=distance,
            )
    return best


def search_registry(search: RegistrySearch) -> RegistryResults:
    params = {
        "query.cond": search.condition,
        "filter.overallStatus": "RECRUITING",
        "countTotal": "true",
        "fields": "NCTId,BriefTitle,OverallStatus",
        "pageSize": "20",
    }
    if search.country:
        params["filter.advanced"] = (
            f'SEARCH[Location](AREA[LocationCountry]COVERAGE[FullMatch]"{search.country}"'
            " AND AREA[LocationStatus]RECRUITING)"
        )
    origin = search.origin
    if origin is None:
        if search.page_token:
            params["pageToken"] = search.page_token
        page = fetch_page(params)
        return RegistryResults(
            trials=page.trials, total_count=page.total_count, next_page_token=page.next_page_token
        )

    # Round away precise device coordinates before forwarding. The farthest rounding
    # shift is under 12 km. Compute the true distance locally for the final cutoff.
    lat, lon = round(origin.latitude, 1), round(origin.longitude, 1)
    params["filter.geo"] = f"distance({lat},{lon},{NEARBY_KM + 12}km)"
    params["fields"] += (
        ",LocationFacility,LocationCity,LocationCountry,LocationStatus,LocationGeoPoint"
    )
    params["pageSize"] = "1000"
    params.pop("countTotal")
    matches: list[tuple[float, RecruitingStudy]] = []
    seen_tokens: set[str] = set()
    while True:
        page = fetch_page(params)
        for study in page.trials:
            site = nearest_site(study, origin, search.country)
            if site:
                study.nearest_site = site
                matches.append((site.distance_km, study))
        if not page.next_page_token:
            break
        if page.next_page_token in seen_tokens:
            raise OSError("ClinicalTrials.gov repeated a page token.")
        seen_tokens.add(page.next_page_token)
        params["pageToken"] = page.next_page_token
    matches.sort(key=lambda match: (match[0], match[1].nct_id))
    return RegistryResults(
        trials=[study for _, study in matches],
        total_count=len(matches),
        next_page_token=None,
        radius_km=NEARBY_KM,
    )
