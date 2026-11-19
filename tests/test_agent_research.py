from datetime import date
from pathlib import Path

import pytest
from test_agent import ApprovingJudge, FakeModel
from test_api import SINGAPORE
from test_approvals import build_approvals, eu_approval, us_approval

from health_compass.agent import (
    LOCAL_APPROVAL,
    Answer,
    AnsweredApproval,
    Draft,
    Intake,
    Note,
    Supervisor,
    medicines,
)
from health_compass.judge import NoteVerdict, Verdicts
from health_compass.research import Paper, PaperResults, PaperSearch, ResearchUnavailableError

SEARCH = Intake(kind="approval_search", condition="Psoriasis", terms=["plaque psoriasis"])
APPROVALS = Draft(
    summary="One medicine is approved for psoriasis.",
    notes=[Note(number=1, note="It is approved for plaque psoriasis.")],
)
PAPER = Paper(
    pmid="40000001",
    title="Skinzumab versus placebo in plaque psoriasis",
    journal="BJD",
    published_on=date(2026, 5, 1),
    paper_type="randomised controlled trial",
    abstract="Skinzumab cleared plaques in more patients than placebo at week 16.",
    source_url="https://europepmc.org/article/MED/40000001",
)
RESEARCH = Draft(
    summary="A recent trial studied skinzumab for psoriasis.",
    notes=[Note(number=1, note="Skinzumab was more effective than placebo at clearing plaques.")],
)
RANKED = Draft(summary="Skinzumab is the best psoriasis medicine.", notes=RESEARCH.notes)


@pytest.fixture(autouse=True)
def tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))
    label = "For plaque psoriasis in adults."
    # dlt only creates the EU table once it has a row: this one doesn't match psoriasis.
    asthma = eu_approval("EMEA/H/C/2", "Breathe", "Authorised", "09/01/2017", "Asthma")
    build_approvals([us_approval("BLA000001", "SKINBRAND", "20240601", label)], [asthma])


class Europepmc:
    def __init__(self, *papers: Paper, down: bool = False) -> None:
        self.papers = list(papers)
        self.down = down
        self.searches: list[PaperSearch] = []

    def __call__(self, search: PaperSearch) -> PaperResults:
        self.searches.append(search)
        if self.down:
            raise ResearchUnavailableError("RuntimeError")
        return PaperResults(papers=self.papers)


def ask(model: FakeModel, europepmc: Europepmc, judge: FakeModel | None = None) -> Answer:
    supervisor = Supervisor(model, judge or ApprovingJudge(), find_papers=europepmc)
    return supervisor.ask("What's approved for psoriasis?", **SINGAPORE)


def test_adds_checked_findings_to_the_approvals_with_the_local_note_last() -> None:
    europepmc = Europepmc(PAPER)
    judge = ApprovingJudge()
    answer = ask(FakeModel(SEARCH, APPROVALS, RESEARCH), europepmc, judge)
    assert answer.outcome == "answered"
    assert answer.message == f"{APPROVALS.summary}\n\n{RESEARCH.summary}\n\n{LOCAL_APPROVAL}"
    assert [a.note for a in answer.approvals] == ["It is approved for plaque psoriasis."]
    [paper] = answer.research
    assert (paper.pmid, paper.medicine, paper.note) == (
        "40000001",
        "something",
        RESEARCH.notes[0].note,
    )
    # Europe PMC gets only the medicine and the condition, and the judge reads the abstract.
    [search] = europepmc.searches
    # The label's active ingredient is SOMETHING.
    assert (search.medicine, search.condition, search.terms) == (
        "something",
        "Psoriasis",
        ["plaque psoriasis"],
    )
    assert PAPER.abstract in judge.prompts[-1]


def test_keeps_the_approvals_answer_when_europe_pmc_is_down() -> None:
    answer = ask(FakeModel(SEARCH, APPROVALS), Europepmc(down=True))
    assert answer.outcome == "answered"
    assert answer.message == f"{APPROVALS.summary}\n\n{LOCAL_APPROVAL}"
    assert answer.research == []


def test_rejects_a_summary_that_ranks_but_lets_a_note_compare_as_its_paper_does() -> None:
    model = FakeModel(SEARCH, APPROVALS, RANKED, RESEARCH)
    answer = ask(model, Europepmc(PAPER))
    assert "it ranks the medicines" in model.prompts[-1]
    assert answer.research[0].note == RESEARCH.notes[0].note


def test_shows_the_papers_without_notes_when_both_research_drafts_fail() -> None:
    rejects = Verdicts(verdicts=[NoteVerdict(number=1, supported=False)])
    approves = Verdicts(verdicts=[NoteVerdict(number=1, supported=True)])
    judge = FakeModel(approves, rejects, rejects)
    answer = ask(FakeModel(SEARCH, APPROVALS, RESEARCH, RESEARCH), Europepmc(PAPER), judge)
    assert answer.outcome == "answered"
    assert answer.message == f"{APPROVALS.summary}\n\n{LOCAL_APPROVAL}"
    assert answer.approvals[0].note is not None
    assert [(p.pmid, p.note) for p in answer.research] == [("40000001", None)]


def test_names_each_medicine_once_as_papers_do() -> None:
    def approval(substance: str | None) -> AnsweredApproval:
        return AnsweredApproval(
            region="US", approval_id="NDA1", medicine_name=None, active_substance=substance,
            holder=None, status="Marketed", approved_on=None, is_conditional=None,
            is_orphan=None, indications=None, source_url=None, note=None,
        )  # fmt: skip

    substances = [
        "METFORMIN HYDROCHLORIDE; SITAGLIPTIN PHOSPHATE",
        "metformin",
        "Omalizumab-igec",
        "RITLECITINIB TOSYLATE",
        None,
    ]
    names = medicines([approval(substance) for substance in substances])
    assert names == ["metformin", "omalizumab", "ritlecitinib"]
