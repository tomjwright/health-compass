import re
import time
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from dbos import DBOS
from fastapi.testclient import TestClient
from test_api import SINGAPORE, site, trial
from test_transform import save_load

from health_compass import agent, transform
from health_compass.agent import Draft, Intake, TrialAgent, TrialNote
from health_compass.api import app, delete_old_runs
from health_compass.judge import NoteVerdict, Verdicts


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


class ApprovingJudge(FakeModel):
    """Stands in for the judge LLM: finds every note supported."""

    def invoke(self, messages: list[tuple[str, str]]) -> Any:
        self.prompts.append("\n".join(text for _, text in messages))
        verdicts = []
        for nct_id in re.findall(r"^(NCT\d{8})$", messages[-1][1], re.MULTILINE):
            verdicts.append(NoteVerdict(nct_id=nct_id, supported=True))
        return Verdicts(verdicts=verdicts)


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


def ask(
    model: FakeModel,
    question: str = "Any hair loss trials near me?",
    judge: FakeModel | None = None,
) -> agent.Answer:
    if judge is None:
        judge = ApprovingJudge()
    return TrialAgent(model, judge).ask(question, **SINGAPORE)


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


def test_retries_when_the_judge_finds_a_note_its_record_does_not_support() -> None:
    model = FakeModel(SEARCH, GOOD, GOOD)
    rejects_one = Verdicts(
        verdicts=[
            NoteVerdict(nct_id="NCT00000011", supported=True),
            NoteVerdict(nct_id="NCT00000012", supported=False),
        ]
    )
    approves_both = Verdicts(
        verdicts=[
            NoteVerdict(nct_id="NCT00000011", supported=True),
            NoteVerdict(nct_id="NCT00000012", supported=True),
        ]
    )
    judge = FakeModel(rejects_one, approves_both)
    answer = ask(model, judge=judge)
    assert answer.outcome == "answered"
    assert "the note on NCT00000012 says something its record doesn't" in model.prompts[-1]
    # The judge sees each trial's record and the note on it, never the location.
    assert "Title: " in judge.prompts[0] and "Note: It studies a hair serum." in judge.prompts[0]
    assert not any("Singapore" in prompt for prompt in judge.prompts)


def test_judge_is_not_asked_when_a_cheap_check_fails() -> None:
    judge = ApprovingJudge()
    ask(FakeModel(SEARCH, DOSING, INVENTED), judge=judge)
    assert judge.prompts == []


def test_shows_only_the_trials_when_both_drafts_fail_the_checks() -> None:
    model = FakeModel(SEARCH, DOSING, INVENTED)
    answer = ask(model)
    assert answer.outcome == "trials_only"
    assert "it mentions a dose; it tells the user what to take" in model.prompts[-1]
    assert [(t.nct_id, t.note) for t in answer.trials] == [
        ("NCT00000011", None),
        ("NCT00000012", None),
    ]


def test_hides_model_output_when_the_llm_fails() -> None:
    model = FakeModel("not an Intake: my hair loss question")
    with pytest.raises(agent.AgentError) as raised:
        ask(model)
    assert str(raised.value) == "the agent failed with ValidationError"
    assert raised.value.__suppress_context__


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[TestClient]:
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path}/dbos.sqlite")
    monkeypatch.setattr("health_compass.agent.judge_model", ApprovingJudge)
    with TestClient(app) as client:
        yield client


def start(api: TestClient) -> str:
    started = api.post("/ask", json={"question": "Hair loss trials?", **SINGAPORE})
    assert started.status_code == 202
    run_id: str = started.json()["run_id"]
    return run_id


def collect(api: TestClient, run_id: str) -> httpx.Response:
    """Polls GET /ask/{run_id} until the run has finished."""
    for _ in range(100):
        response = api.get(f"/ask/{run_id}")
        if response.status_code != 200 or response.json()["status"] == "done":
            return response
        time.sleep(0.1)
    raise AssertionError("the run did not finish")


def test_ask_endpoint_answers_and_keeps_the_answer_until_the_run_expires(
    monkeypatch: pytest.MonkeyPatch, api: TestClient
) -> None:
    monkeypatch.setattr("health_compass.agent.chat_model", lambda: FakeModel(SEARCH, GOOD))
    run_id = start(api)
    body = collect(api, run_id).json()
    assert body["answer"]["outcome"] == "answered"
    assert body["steps_done"] == ["check_input", "find_trials", "write_answer", "check_output"]
    # Reading the answer again (say, after a refresh) still works.
    assert api.get(f"/ask/{run_id}").json()["answer"]["outcome"] == "answered"


def test_ask_says_when_the_tables_are_not_built(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, api: TestClient
) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "missing.duckdb"))
    monkeypatch.setattr("health_compass.agent.chat_model", lambda: FakeModel(SEARCH))
    assert collect(api, start(api)).status_code == 503


def test_runs_are_deleted_after_an_hour(monkeypatch: pytest.MonkeyPatch, api: TestClient) -> None:
    monkeypatch.setattr("health_compass.agent.chat_model", lambda: FakeModel(SEARCH, GOOD))
    run_id = start(api)
    DBOS.retrieve_workflow(run_id).get_result()
    delete_old_runs(datetime.now(UTC))
    assert len(DBOS.list_workflows(workflow_ids=[run_id])) == 1
    delete_old_runs(datetime.now(UTC) + timedelta(minutes=61))
    assert DBOS.list_workflows(workflow_ids=[run_id]) == []
    assert api.get(f"/ask/{run_id}").status_code == 404


def test_a_resumed_run_does_not_ask_the_llm_again_for_finished_steps(
    monkeypatch: pytest.MonkeyPatch, api: TestClient
) -> None:
    model = FakeModel(SEARCH, GOOD, GOOD)
    monkeypatch.setattr("health_compass.agent.chat_model", lambda: model)
    run_id = start(api)
    DBOS.retrieve_workflow(run_id).get_result()
    assert len(model.prompts) == 2
    # Restart the run from write_answer: DBOS replays the steps before it from their saved
    # results, as it does when it resumes a run after a crash.
    write_answer = 0
    for step in DBOS.list_workflow_steps(run_id):
        if step["function_name"].endswith("write_answer"):
            write_answer = step["function_id"]
    answer = DBOS.fork_workflow(run_id, write_answer).get_result()
    assert answer.outcome == "answered"
    assert len(model.prompts) == 3
    assert "Trials:" in model.prompts[-1]
