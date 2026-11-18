from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from test_api import SINGAPORE, site, trial
from test_transform import save_load

from health_compass import agent, transform
from health_compass.agent import Draft, Intake, TrialAgent, TrialNote
from health_compass.api import app


class FakeModel:
    """Stands in for the LLM: returns the given replies in order and keeps every prompt."""

    def __init__(self, *replies: Any) -> None:
        self.replies = list(replies)
        self.prompts: list[str] = []

    def with_structured_output(self, schema: type) -> "FakeModel":
        return self

    def invoke(self, messages: list[tuple[str, str]]) -> Any:
        self.prompts.append("\n".join(text for _, text in messages))
        return self.replies.pop(0)


SEARCH = Intake(kind="trial_search", condition="alopecia")
GOOD = Draft(
    summary="Two trials match. Talk to your doctor before joining one.",
    notes=[
        TrialNote(nct_id="NCT00000011", note="It studies a hair serum."),
        TrialNote(nct_id="NCT00000012", note="It studies a light treatment."),
    ],
)
INVENTED = Draft(summary="One trial.", notes=[TrialNote(nct_id="NCT09999999", note="Made up.")])
DOSING = Draft(summary="You should take 5 mg a day.", notes=[])


@pytest.fixture(autouse=True)
def tables(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("RAW_DATA_DIR", str(tmp_path / "raw"))
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))
    near = trial("NCT00000011", site("Singapore", 1.29, 103.85))
    far = trial("NCT00000012", site("London", 51.51, -0.13))
    save_load("2026-11-16", [near, far])
    transform.build()


def ask(model: FakeModel, question: str = "Any hair loss trials near me?") -> agent.Answer:
    return TrialAgent(model).ask(question, **SINGAPORE)


def test_answers_with_facts_from_the_tables_and_notes_from_the_llm() -> None:
    model = FakeModel(SEARCH, GOOD)
    answer = ask(model)
    assert answer.outcome == "answered"
    assert answer.message == GOOD.summary
    assert [(t.nct_id, t.city, t.is_recruiting, t.note) for t in answer.trials] == [
        ("NCT00000011", "Singapore", True, "It studies a hair serum."),
        ("NCT00000012", "London", True, "It studies a light treatment."),
    ]
    # The LLM never sees the location.
    assert not any("1.29" in prompt or "Singapore" in prompt for prompt in model.prompts)


def test_refuses_a_dosing_question_without_searching() -> None:
    model = FakeModel(Intake(kind="personal_medical_advice", condition="alopecia"))
    answer = ask(model, "How much minoxidil should I take?")
    assert answer.outcome == "refused"
    assert "doctor or pharmacist" in answer.message
    assert answer.trials == []


def test_says_when_no_trials_match() -> None:
    answer = ask(FakeModel(Intake(kind="trial_search", condition="asthma")))
    assert answer.outcome == "no_trials"


def test_retries_once_when_a_draft_cites_a_trial_it_was_not_given() -> None:
    model = FakeModel(SEARCH, INVENTED, GOOD)
    answer = ask(model)
    assert answer.outcome == "answered"
    assert "NCT09999999 is not one of the trials given" in model.prompts[-1]


def test_shows_only_the_trials_when_both_drafts_fail_the_checks() -> None:
    model = FakeModel(SEARCH, DOSING, INVENTED)
    answer = ask(model)
    assert answer.outcome == "trials_only"
    assert "it mentions a dose; it tells the user what to take" in model.prompts[-1]
    assert [(t.nct_id, t.note) for t in answer.trials] == [
        ("NCT00000011", None),
        ("NCT00000012", None),
    ]


def test_ask_endpoint(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("health_compass.api.chat_model", lambda: FakeModel(SEARCH, GOOD))
    response = TestClient(app).post("/ask", json={"question": "Hair loss trials?", **SINGAPORE})
    assert response.status_code == 200
    assert response.json()["outcome"] == "answered"


def test_hides_model_output_when_the_llm_fails() -> None:
    model = FakeModel("not an Intake: my hair loss question")
    with pytest.raises(agent.AgentError) as raised:
        ask(model)
    assert str(raised.value) == "the agent failed with ValidationError"
    assert raised.value.__suppress_context__


def test_ask_says_when_the_tables_are_not_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setattr("health_compass.api.chat_model", lambda: FakeModel(SEARCH))
    response = TestClient(app).post("/ask", json={"question": "Hair loss trials?", **SINGAPORE})
    assert response.status_code == 503
