"""Find the newest systematic reviews and randomised trials of a medicine for a condition, from
Europe PMC, which includes all of PubMed.

Unlike trials and approvals, papers are searched live: there are too many to load. Only the
medicine's and the condition's names are sent, never the user's question or location.
"""

import logging
import re
from datetime import date
from typing import Annotated, Any

import httpx
from pydantic import BaseModel, Field

from health_compass.ingest import common

log = logging.getLogger(__name__)

SEARCH_URL = "https://www.ebi.ac.uk/europepmc/webservices/rest/search"
TIMEOUT_SECONDS = 30
# Europe PMC's publication types, strongest evidence first: a paper is labelled with the first
# one it has.
PAPER_TYPES = {
    "Systematic Review": "systematic review",
    "Meta-Analysis": "meta-analysis",
    "Randomized Controlled Trial": "randomised controlled trial",
}
TAG = re.compile(r"<[^>]+>")

Name = Annotated[str, Field(min_length=2, max_length=100)]


class ResearchUnavailableError(Exception):
    """Europe PMC couldn't be reached, or sent something unexpected."""


class PaperSearch(BaseModel):
    """A medicine and a condition. Sent as a POST body, so it stays out of URLs and traces."""

    medicine: Name = Field(description="The active substance.", examples=["tirzepatide"])
    condition: Name = Field(
        description="The condition's MeSH heading.", examples=["Diabetes Mellitus, Type 2"]
    )
    terms: list[Name] = Field(
        default=[],
        max_length=3,
        description="Other words for the condition.",
        examples=[["type 2 diabetes"]],
    )
    limit: int = Field(default=2, ge=1, le=10)


class Paper(BaseModel):
    pmid: str
    title: str
    journal: str | None
    published_on: date | None
    paper_type: str
    abstract: str
    source_url: str


class PaperResults(BaseModel):
    papers: list[Paper]


def http_client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT_SECONDS, headers={"User-Agent": "health-compass"})


def search_query(search: PaperSearch) -> str:
    """Papers with the medicine in the title and the condition in the title or abstract."""

    def phrase(field: str, text: str) -> str:
        return f'{field}:"{text.replace(chr(34), " ")}"'

    conditions = [phrase("TITLE_ABS", name) for name in [search.condition, *search.terms]]
    types = [phrase("PUB_TYPE", paper_type) for paper_type in PAPER_TYPES]
    return (
        f"{phrase('TITLE', search.medicine)} AND ({' OR '.join(conditions)}) "
        f"AND ({' OR '.join(types)}) AND SRC:MED AND HAS_ABSTRACT:y"
    )


def find_papers(search: PaperSearch) -> PaperResults:
    """The newest reviews and randomised trials of the medicine for the condition."""
    params: dict[str, str | int] = {
        "query": search_query(search),
        "format": "json",
        "resultType": "core",
        "sort": "P_PDATE_D desc",
        "pageSize": search.limit,
    }

    def attempt() -> Any:
        with http_client() as client:
            response = client.get(SEARCH_URL, params=params)
        if response.is_error:
            raise RuntimeError(f"HTTP {response.status_code} from Europe PMC")
        return response.json()

    try:
        found = common.with_retries(SEARCH_URL, attempt)
        papers = [paper(result) for result in found["resultList"]["result"]]
    except (httpx.HTTPError, RuntimeError, ValueError, KeyError) as error:
        # Only the error's type is kept: it is saved by DBOS and must be picklable.
        raise ResearchUnavailableError(type(error).__name__) from None
    log.info("found %d papers", len(papers))
    return PaperResults(papers=papers)


def paper(result: dict[str, Any]) -> Paper:
    types = result.get("pubTypeList", {}).get("pubType", [])
    published = result.get("firstPublicationDate")
    return Paper(
        pmid=result["pmid"],
        title=plain(result["title"]),
        journal=result.get("journalInfo", {}).get("journal", {}).get("title"),
        published_on=date.fromisoformat(published) if published else None,
        paper_type=next(label for name, label in PAPER_TYPES.items() if name in types),
        abstract=plain(result["abstractText"]),
        source_url=f"https://europepmc.org/article/MED/{result['pmid']}",
    )


def plain(text: str) -> str:
    """Europe PMC marks up titles and abstracts with HTML, e.g. <h4>Methods</h4>."""
    return " ".join(TAG.sub(" ", text).split())
