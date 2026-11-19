from pathlib import Path

import pytest
from test_agent import ApprovingJudge, FakeModel
from test_approvals import build_approvals, eu_approval, us_approval

from health_compass.approval_agent import (
    LOCAL_APPROVAL,
    ApprovalAgent,
    ApprovalAnswer,
    ApprovalNote,
    Draft,
    Intake,
)
from health_compass.judge import NoteVerdict, Verdicts

LONG_LABEL = "For plaque psoriasis in adults. " + "Warnings and doses follow. " * 100 + "TAIL"
SEARCH = Intake(kind="approval_search", condition="Psoriasis", terms=["plaque psoriasis"])
GOOD = Draft(
    summary="Two medicines are approved for psoriasis. Talk to your doctor about your options.",
    notes=[
        ApprovalNote(approval_id="NDA000001", note="It is approved for plaque psoriasis."),
        ApprovalNote(approval_id="EMEA/H/C/1", note="It is approved for psoriasis."),
    ],
)
RANKED = Draft(summary="Eudrug is the best psoriasis medicine.", notes=[])
INVENTED = Draft(
    summary="One medicine.", notes=[ApprovalNote(approval_id="NDA099999", note="Made up.")]
)


@pytest.fixture(autouse=True)
def tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))
    us = [us_approval("NDA000001", "SKINBRAND", "20240601", LONG_LABEL)]
    eu = [eu_approval("EMEA/H/C/1", "Eudrug", "Authorised", "09/01/2017", "Psoriasis")]
    build_approvals(us, eu)


def ask(model: FakeModel, question: str, judge: FakeModel | None = None) -> ApprovalAnswer:
    return ApprovalAgent(model, judge or ApprovingJudge()).ask(question)


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
    # The LLM and the judge see only the start of a long label.
    for prompt in [model.prompts[-1], judge.prompts[0]]:
        assert "For plaque psoriasis in adults." in prompt
        assert "TAIL" not in prompt


@pytest.mark.parametrize(
    ("kind", "question", "says"),
    [
        ("personal_medical_advice", "Which psoriasis drug should I take?", "doctor or pharmacist"),
        ("other", "What is Sotyktu approved for?", "I can only list"),
    ],
)
def test_refuses_advice_and_questions_about_a_named_medicine(
    kind: str, question: str, says: str
) -> None:
    answer = ask(FakeModel(Intake(kind=kind, condition=None)), question)  # type: ignore[arg-type]
    assert answer.outcome == "refused"
    assert says in answer.message
    assert answer.approvals == []


def test_says_when_nothing_is_approved_and_to_check_locally() -> None:
    answer = ask(FakeModel(Intake(kind="approval_search", condition="Progeria")), "Progeria?")
    assert answer.outcome == "no_approvals"
    assert "local medicines regulator" in answer.message


def test_retries_when_the_judge_finds_a_note_its_approval_does_not_support() -> None:
    model = FakeModel(SEARCH, GOOD, GOOD)
    rejects_one = Verdicts(
        verdicts=[
            NoteVerdict(id="NDA000001", supported=False),
            NoteVerdict(id="EMEA/H/C/1", supported=True),
        ]
    )
    approves_both = Verdicts(
        verdicts=[
            NoteVerdict(id="NDA000001", supported=True),
            NoteVerdict(id="EMEA/H/C/1", supported=True),
        ]
    )
    judge = FakeModel(rejects_one, approves_both)
    answer = ask(model, "What's approved for psoriasis?", judge)
    assert answer.outcome == "answered"
    assert "the note on NDA000001 says something its record doesn't" in model.prompts[-1]


def test_shows_only_the_approvals_when_both_drafts_fail_the_checks() -> None:
    model = FakeModel(SEARCH, RANKED, INVENTED)
    judge = ApprovingJudge()
    answer = ask(model, "What's approved for psoriasis?", judge)
    assert answer.outcome == "approvals_only"
    assert answer.message.endswith(LOCAL_APPROVAL)
    assert "it ranks the medicines" in model.prompts[-1]
    assert [a.note for a in answer.approvals] == [None, None]
    assert judge.prompts == []
