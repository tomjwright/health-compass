"""Search the live ClinicalTrials.gov registry for recruiting studies worldwide."""

import urllib.request
from typing import Literal
from urllib.parse import urlencode

from pydantic import AliasPath, BaseModel, Field

from health_compass.ingest.clinicaltrials import API


class WorldwideSearch(BaseModel):
    condition: str = Field(min_length=2, max_length=100)
    page_token: str | None = Field(default=None, min_length=1, max_length=512)


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


class WorldwideResults(BaseModel):
    trials: list[RecruitingStudy] = Field(validation_alias="studies")
    # The registry includes the total on the first page only.
    total_count: int | None = Field(default=None, validation_alias="totalCount")
    next_page_token: str | None = Field(default=None, validation_alias="nextPageToken")


def search_registry(search: WorldwideSearch) -> WorldwideResults:
    params = {
        "query.cond": search.condition,
        "filter.overallStatus": "RECRUITING",
        "countTotal": "true",
        "fields": "NCTId,BriefTitle,OverallStatus",
        "pageSize": "20",
    }
    if search.page_token:
        params["pageToken"] = search.page_token
    url = f"{API}/studies?{urlencode(params)}"
    # Keep the outgoing URL out of logs. The HTTPS host is fixed and all parameters are encoded.
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310
        return WorldwideResults.model_validate_json(response.read())
