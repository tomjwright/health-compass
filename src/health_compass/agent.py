"""The first agent: answers "are there trials for X near me?" from the trial tables.

    check_input ──► find_trials ──► write_answer ──► check_output ──► answered
         │               │               ▲                 │
         ▼               ▼               └── fails once ───┤
      refused        no_trials                fails twice ─┴──► trials_only

The LLM only sorts the question and writes one note per trial. Python decides what is refused
and what is shown, and every fact shown (status, site, distance) comes from the tables.
The question goes to the LLM; the location never does, and neither is logged or traced.

`answer_question` runs the agent as a durable DBOS workflow. Each node is a DBOS step, so after
a crash a run resumes from its last finished step, without calling the LLM again for the others.
"""

import logging
import os
import re
from typing import Literal, TypedDict

from dbos import DBOS
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, Field

from health_compass.judge import unsupported_notes
from health_compass.trials import TablesNotBuiltError, Trial, TrialSearch, find_trials

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

DEFAULT_MODEL = "google_genai:gemini-3.1-flash-lite"
MAX_TRIALS = 5
MAX_ANSWER_ATTEMPTS = 2

REFUSALS = {
    "personal_medical_advice": (
        "I can't advise on doses or on whether you should take a medicine: please ask your "
        "doctor or pharmacist. I can find clinical trials for a condition near you."
    ),
    "other": (
        "I can only find clinical trials for a medical condition near you, for example "
        '"Are there alopecia trials near me?"'
    ),
}
TRIALS_ONLY = (
    "Here are the trials I found. I couldn't write a summary that passed my checks, "
    "so only the trial details are shown."
)

INTAKE_PROMPT = """Sort the user's question for a clinical trial finder.
- trial_search: they want clinical trials for a medical condition.
- personal_medical_advice: they ask about doses, or whether they should take, start or stop
  a treatment.
- other: anything else.
For trial_search, give the condition as its official MeSH heading, e.g. "Alopecia" for
"hair loss" or "Hypertension" for "high blood pressure"."""

ANSWER_PROMPT = """You describe clinical trials to a member of the public.
Use only the trials given. Never give doses or advice on what to take.
Write a one or two sentence summary that suggests discussing trials with their doctor, and one
plain-English sentence per trial on what it studies, using its NCT ID exactly as given."""

DOSE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|µg|ml|iu|units?)\b", re.IGNORECASE)
ADVICE = re.compile(r"\byou should (take|start|stop|use)\b|\b(take|increase|reduce) your\b", re.I)


def chat_model() -> BaseChatModel:
    """The LLM in LLM_MODEL, as "provider:model", e.g. "anthropic:claude-haiku-4-5".

    Another provider needs its LangChain package (e.g. langchain-anthropic) and API key.
    """
    return init_chat_model(os.environ.get("LLM_MODEL", DEFAULT_MODEL), temperature=0)


def judge_model() -> BaseChatModel:
    """The LLM in JUDGE_MODEL, which checks the notes, or the agent's LLM if it isn't set."""
    name = os.environ.get("JUDGE_MODEL")
    if name is None:
        return chat_model()
    return init_chat_model(name, temperature=0)


class AgentError(Exception):
    """The agent failed. It carries no model output, so it is safe to log and trace."""


class Intake(BaseModel):
    kind: Literal["trial_search", "personal_medical_advice", "other"]
    condition: str | None = Field(description="The condition's MeSH heading, or null.")


class TrialNote(BaseModel):
    nct_id: str
    note: str = Field(description="One sentence on what the trial studies.")


class Draft(BaseModel):
    summary: str
    notes: list[TrialNote]


class AnsweredTrial(Trial):
    note: str | None


class Answer(BaseModel):
    outcome: Literal["answered", "refused", "no_trials", "trials_only"]
    message: str
    trials: list[AnsweredTrial]


class State(TypedDict, total=False):
    question: str
    latitude: float
    longitude: float
    condition: str
    trials: list[Trial]
    draft: Draft
    problems: list[str]
    attempts: int
    answer: Answer


class TrialAgent:
    def __init__(self, model: BaseChatModel, judge: BaseChatModel) -> None:
        self.model = model
        self.judge = judge
        graph = StateGraph(State)
        # LLM calls are retried, with backoff, when the provider is briefly overloaded.
        llm_retry = RetryPolicy(max_attempts=3)
        graph.add_node("check_input", self.check_input, retry_policy=llm_retry)
        graph.add_node("find_trials", self.find_trials)
        graph.add_node("write_answer", self.write_answer, retry_policy=llm_retry)
        graph.add_node("check_output", self.check_output, retry_policy=llm_retry)
        graph.add_edge(START, "check_input")
        # Each gateway either sets the answer, which ends the run, or lets the run go on.
        graph.add_conditional_edges("check_input", after_check_input)
        graph.add_conditional_edges("find_trials", after_find_trials)
        graph.add_edge("write_answer", "check_output")
        graph.add_conditional_edges("check_output", after_check_output)
        self.graph = graph.compile()

    def ask(self, question: str, latitude: float, longitude: float) -> Answer:
        with tracer.start_as_current_span(
            "agent.ask", record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                state = self.graph.invoke(
                    {"question": question, "latitude": latitude, "longitude": longitude}
                )
            except TablesNotBuiltError:
                raise
            except Exception as error:
                # LLM errors can quote the model's output, which can repeat the question, so
                # only the error's type reaches traces and logs.
                span.set_status(StatusCode.ERROR, type(error).__name__)
                raise AgentError(f"the agent failed with {type(error).__name__}") from None
            answer: Answer = state["answer"]
            span.set_attribute("agent.outcome", answer.outcome)
        log.info("agent outcome: %s", answer.outcome)
        return answer

    @DBOS.step()
    def check_input(self, state: State) -> State:
        """Gateway: only trial searches go on; anything else gets a fixed refusal."""
        with tracer.start_as_current_span(
            "agent.check_input", record_exception=False, set_status_on_exception=False
        ) as span:
            reply = self.model.with_structured_output(Intake).invoke(
                [("system", INTAKE_PROMPT), ("human", state["question"])]
            )
            intake = Intake.model_validate(reply)
            span.set_attribute("agent.kind", intake.kind)
        if intake.kind == "trial_search" and intake.condition:
            return {"condition": intake.condition[:100]}
        kind = "other" if intake.kind == "trial_search" else intake.kind
        return {"answer": Answer(outcome="refused", message=REFUSALS[kind], trials=[])}

    @DBOS.step()
    def find_trials(self, state: State) -> State:
        with tracer.start_as_current_span("agent.find_trials") as span:
            search = TrialSearch(
                condition=state["condition"],
                latitude=state["latitude"],
                longitude=state["longitude"],
                limit=MAX_TRIALS,
            )
            trials = find_trials(search).trials
            span.set_attribute("agent.trials_found", len(trials))
        if trials:
            return {"trials": list(trials)}
        message = f"I found no trials for {state['condition']} in the trial data."
        return {"answer": Answer(outcome="no_trials", message=message, trials=[])}

    @DBOS.step()
    def write_answer(self, state: State) -> State:
        """The LLM sees each trial's ID, title and status: not its site or distance."""
        with tracer.start_as_current_span(
            "agent.write_answer", record_exception=False, set_status_on_exception=False
        ) as span:
            lines = []
            for trial in state["trials"]:
                lines.append(f"{trial.nct_id}: {trial.title} ({trial.overall_status})")
            trials = "\n".join(lines)
            messages = [
                ("system", ANSWER_PROMPT),
                ("human", f"Question: {state['question']}\n\nTrials:\n{trials}"),
            ]
            if state.get("problems"):
                problems = "; ".join(state["problems"])
                messages.append(("human", f"Your last answer was rejected: {problems}. Redo it."))
            reply = self.model.with_structured_output(Draft).invoke(messages)
            # Some providers return a dict rather than the model: validating makes it a Draft.
            draft = Draft.model_validate(reply)
            attempts = state.get("attempts", 0) + 1
            span.set_attribute("agent.attempt", attempts)
        return {"draft": draft, "attempts": attempts}

    @DBOS.step()
    def check_output(self, state: State) -> State:
        """Gateway: the draft must cite only the trials found, give no doses or advice, and
        say nothing about a trial that its record doesn't (checked by the judge LLM)."""
        draft = state["draft"]
        trials = state["trials"]
        found = set()
        for trial in trials:
            found.add(trial.nct_id)

        problems = []
        texts = [draft.summary]
        notes = {}
        for trial_note in draft.notes:
            if trial_note.nct_id not in found:
                problems.append(f"{trial_note.nct_id} is not one of the trials given")
            texts.append(trial_note.note)
            notes[trial_note.nct_id] = trial_note.note

        mentions_a_dose = False
        gives_advice = False
        for text in texts:
            if DOSE.search(text):
                mentions_a_dose = True
            if ADVICE.search(text):
                gives_advice = True
        if mentions_a_dose:
            problems.append("it mentions a dose")
        if gives_advice:
            problems.append("it tells the user what to take")

        with tracer.start_as_current_span(
            "agent.check_output", record_exception=False, set_status_on_exception=False
        ) as span:
            # The judge is only asked once the cheap checks pass.
            unsupported = []
            if not problems and notes:
                unsupported = unsupported_notes(self.judge, trials, notes)
            for nct_id in unsupported:
                problems.append(f"the note on {nct_id} says something its record doesn't")
            span.set_attribute("agent.passed", not problems)
            span.set_attribute("agent.problems", len(problems))
            span.set_attribute("agent.unsupported_notes", len(unsupported))

        if not problems:
            answered = add_notes(trials, notes)
            return {"answer": Answer(outcome="answered", message=draft.summary, trials=answered)}
        # The problems quote model output, so only their number is logged.
        log.warning("answer failed %d checks", len(problems))
        if state["attempts"] < MAX_ANSWER_ATTEMPTS:
            return {"problems": problems}
        bare = add_notes(trials, {})
        return {"answer": Answer(outcome="trials_only", message=TRIALS_ONLY, trials=bare)}


@DBOS.workflow()
def answer_question(question: str, latitude: float, longitude: float) -> Answer:
    """`TrialAgent.ask` as a durable run. DBOS saves the question, location and each step's result
    until `api.py` deletes the run, after RUN_TTL_MINUTES (60 by default).
    """
    return TrialAgent(chat_model(), judge_model()).ask(question, latitude, longitude)


def add_notes(trials: list[Trial], notes: dict[str, str]) -> list[AnsweredTrial]:
    """Each trial as found in the tables, with the LLM's note on it, if any."""
    answered = []
    for trial in trials:
        fields = trial.model_dump()
        fields["note"] = notes.get(trial.nct_id)
        answered.append(AnsweredTrial(**fields))
    return answered


def after_check_input(state: State) -> str:
    return END if "answer" in state else "find_trials"


def after_find_trials(state: State) -> str:
    return END if "answer" in state else "write_answer"


def after_check_output(state: State) -> str:
    """A rejected draft goes back to write_answer, with the problems found."""
    return END if "answer" in state else "write_answer"
