from collections.abc import Callable
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from health_compass import research
from health_compass.api import app
from health_compass.research import PaperSearch, ResearchUnavailableError, find_papers

RESULT = {
    "pmid": "40000001",
    "title": "Tirzepatide in type 2 diabetes: a &lt;i&gt;systematic&lt;/i&gt; review",
    "journalInfo": {"journal": {"title": "Diabetes Care"}},
    "firstPublicationDate": "2026-05-01",
    "pubTypeList": {"pubType": ["Meta-Analysis", "Systematic Review", "Journal Article"]},
    "abstractText": "<h4>Results</h4>Tirzepatide lowered HbA1c   more than placebo.",
}
SEARCH = PaperSearch(medicine="tirzepatide", condition="Diabetes Mellitus, Type 2", terms=["T2D"])


Replier = Callable[..., list[httpx.Request]]


@pytest.fixture
def europepmc(monkeypatch: pytest.MonkeyPatch) -> Replier:
    """Makes Europe PMC send `responses` in turn; returns the requests it was sent."""
    monkeypatch.setattr("time.sleep", lambda seconds: None)

    def replies(*responses: httpx.Response) -> list[httpx.Request]:
        requests: list[httpx.Request] = []
        queued = iter(responses)

        def reply(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return next(queued)

        client = lambda: httpx.Client(transport=httpx.MockTransport(reply))  # noqa: E731
        monkeypatch.setattr(research, "http_client", client)
        return requests

    return replies


def found(*results: dict[str, Any]) -> httpx.Response:
    return httpx.Response(200, json={"resultList": {"result": list(results)}})


def test_sends_only_the_names_and_keeps_reviews_and_trials_newest_first(
    europepmc: Replier,
) -> None:
    requests = europepmc(found(RESULT))
    papers = find_papers(SEARCH).papers
    params = requests[0].url.params
    assert params["query"] == (
        'TITLE:"tirzepatide" AND (TITLE_ABS:"Diabetes Mellitus, Type 2" OR TITLE_ABS:"T2D") '
        'AND (PUB_TYPE:"Systematic Review" OR PUB_TYPE:"Meta-Analysis" '
        'OR PUB_TYPE:"Randomized Controlled Trial") AND SRC:MED AND HAS_ABSTRACT:y'
    )
    assert (params["sort"], params["pageSize"]) == ("FIRST_PDATE_D desc", "2")
    [paper] = papers
    # The strongest type of evidence is kept, and the markup is removed.
    assert paper.paper_type == "systematic review"
    assert paper.title == "Tirzepatide in type 2 diabetes: a systematic review"
    assert paper.abstract == "Results Tirzepatide lowered HbA1c more than placebo."
    assert paper.source_url == "https://europepmc.org/article/MED/40000001"
    assert str(paper.published_on) == "2026-05-01"


def test_reads_types_written_with_hyphens_and_skips_papers_of_other_types(
    europepmc: Replier,
) -> None:
    hyphenated = {**RESULT, "pmid": "2", "pubTypeList": {"pubType": ["systematic-review"]}}
    other = {**RESULT, "pmid": "3", "pubTypeList": {"pubType": ["Journal Article"]}}
    europepmc(found(hyphenated, other))
    papers = find_papers(SEARCH).papers
    assert [(paper.pmid, paper.paper_type) for paper in papers] == [("2", "systematic review")]


def test_a_quote_in_a_name_cannot_change_the_query() -> None:
    query = research.search_query(PaperSearch(medicine='x" OR "y', condition="Psoriasis"))
    assert query.startswith('TITLE:"x  OR  y" AND (TITLE_ABS:"Psoriasis")')


def test_retries_then_raises_an_error_that_carries_nothing_from_europe_pmc(
    europepmc: Replier,
) -> None:
    requests = europepmc(*[httpx.Response(503)] * 3)
    with pytest.raises(ResearchUnavailableError) as raised:
        find_papers(SEARCH)
    assert len(requests) == 3
    assert str(raised.value) == "RuntimeError"


def test_research_endpoint_returns_503_when_europe_pmc_is_down(europepmc: Replier) -> None:
    europepmc(*[httpx.Response(500)] * 3)
    body = {"medicine": "tirzepatide", "condition": "Diabetes Mellitus, Type 2"}
    response = TestClient(app).post("/research", json=body)
    assert response.status_code == 503
