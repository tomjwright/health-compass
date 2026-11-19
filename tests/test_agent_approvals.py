from pathlib import Path

import pytest
from test_agent import ApprovingJudge, FakeModel
from test_api import SINGAPORE
from test_approvals import build_approvals, eu_approval, us_approval

from health_compass.agent import LOCAL_APPROVAL, Answer, Draft, Intake, Note, Supervisor
from health_compass.judge import NoteVerdict, Verdicts

LONG_LABEL = "For plaque psoriasis in adults. " + "Warnings and doses follow. " * 100 + "TAIL"
SEARCH = Intake(kind="approval_search", condition="Psoriasis", terms=["plaque psoriasis"])
GOOD = Draft(
    summary="Two medicines are approved for psoriasis. Talk to your doctor about your options.",
    notes=[
        Note(number=1, note="It is approved for plaque psoriasis."),
        Note(number=2, note="It is approved for psoriasis."),
    ],
)
RANKED = Draft(summary="Eudrug is the best psoriasis medicine.", notes=[])
INVENTED = Draft(summary="One medicine.", notes=[Note(number=3, note="Made up.")])


@pytest.fixture(autouse=True)
def tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))
    us = [us_approval("NDA000001", "SKINBRAND", "20240601", LONG_LABEL)]
    eu = [eu_approval("EMEA/H/C/1", "Eudrug", "Authorised", "09/01/2017", "Psoriasis")]
    build_approvals(us, eu)


def ask(model: FakeModel, question: str, judge: FakeModel | None = None) -> Answer:
    return Supervisor(model, judge or ApprovingJudge()).ask(question, **SINGAPORE)


def test_answers_with_facts_from_the_tables_and_says_to_check_local_approval() -> None:
    model = FakeModel(SEARCH, GOOD)
    judge = ApprovingJudge()
    answer = ask(model, "What's approved for psoriasis?", judge)
    assert answer.outcome == "answered"
    assert answer.message == f"{GOOD.summary}\n\n{LOCAL_APPROVAL}"
    assert [(a.region, a.approval_id, a.status, a.note) for a in answer.approvals] == [
        ("US", "NDA000001", "Marketed", "It is approved for plaque psoriasis."),
        ("EU", "EMEA/H/C/1", "Authorised", "It is approved for psoriasis."),
    ]
    # The LLM and the judge see only the start of a long label, and never the location.
    for prompt in [model.prompts[-1], judge.prompts[0]]:
        assert "For plaque psoriasis in adults." in prompt
        assert "TAIL" not in prompt
        assert "1.29" not in prompt


@pytest.mark.parametrize(
    ("kind", "question", "says"),
    [
        ("personal_medical_advice", "Which psoriasis medicine should I take?", "pharmacist"),
        ("other", "What is Sotyktu approved for?", "I can only"),
    ],
)
def test_refuses_advice_and_named_medicine_questions(kind: str, question: str, says: str) -> None:
    answer = ask(FakeModel(Intake(kind=kind, condition=None)), question)  # type: ignore[arg-type]
    assert answer.outcome == "refused"
    assert says in answer.message
    assert answer.approvals == []


def test_says_when_no_approvals_match() -> None:
    answer = ask(FakeModel(Intake(kind="approval_search", condition="Asthma")), "Asthma?")
    assert answer.outcome == "no_approvals"
    assert "local medicines regulator" in answer.message


def test_retries_when_the_judge_finds_a_note_its_approval_does_not_support() -> None:
    model = FakeModel(SEARCH, GOOD, GOOD)
    rejects_one = Verdicts(
        verdicts=[NoteVerdict(number=1, supported=False), NoteVerdict(number=2, supported=True)]
    )
    approves_both = Verdicts(
        verdicts=[NoteVerdict(number=1, supported=True), NoteVerdict(number=2, supported=True)]
    )
    answer = ask(model, "What's approved for psoriasis?", FakeModel(rejects_one, approves_both))
    assert answer.outcome == "answered"
    assert "the note on 1 says something its record doesn't" in model.prompts[-1]


def test_shows_only_the_approvals_when_both_drafts_fail_the_checks() -> None:
    model = FakeModel(SEARCH, RANKED, INVENTED)
    judge = ApprovingJudge()
    answer = ask(model, "What's approved for psoriasis?", judge)
    assert answer.outcome == "approvals_only"
    assert answer.message.endswith(LOCAL_APPROVAL)
    assert "it ranks the medicines" in model.prompts[-1]
    assert [a.note for a in answer.approvals] == [None, None]
    assert judge.prompts == []
