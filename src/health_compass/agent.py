"""The agent: one graph that answers questions from the tables, such as "are there trials for X
near me?".

    route ──► find_trials ──► write_answer ──► check_output ──► answered
      │            │               ▲                 │
      ▼            ▼               └── fails once ───┤
   refused     no_trials                fails twice ─┴──► trials_only

`route` sorts the question and sends it to the search for its kind of record. Each search is
its own node; `write_answer` and `check_output` are shared, and take what differs between kinds
of record (prompts, how a record is shown, the judge's prompt) from that kind's `Source`.

The LLM only sorts the question and writes one note per record. Python decides what is refused
and what is shown, and every fact shown (status, site, distance) comes from the tables.
The question goes to the LLM; the location never does, and neither is logged or traced.

`answer_question` runs the agent as a durable DBOS workflow. Each node is a DBOS step, so after
a crash a run resumes from its last finished step, without calling the LLM again for the others.
"""

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Literal, TypedDict

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
plain-English sentence per trial on what it studies, citing it by its number."""

TRIAL_JUDGE_PROMPT = """You check notes written about clinical trials for the public.
Each trial comes with its record from ClinicalTrials.gov and a note about it. A note is
supported if everything it claims is stated in, or follows directly from, the record. Plain
paraphrase is fine. A note that adds a result, a benefit, or a detail that isn't in the record
is unsupported."""

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


class Note(BaseModel):
    number: int
    note: str = Field(description="One sentence on what the record is about.")


class Draft(BaseModel):
    summary: str
    notes: list[Note]


class AnsweredTrial(Trial):
    note: str | None


class Answer(BaseModel):
    outcome: Literal["answered", "refused", "no_trials", "trials_only"]
    message: str
    trials: list[AnsweredTrial] = []


@dataclass(frozen=True)
class Source:
    """What the shared nodes need to know about one kind of record."""

    name: str  # the Answer field the records go in, e.g. "trials"
    kind: str  # the Intake kind that searches it
    answer_prompt: str
    judge_prompt: str
    shown_to_writer: Callable[[Any], str]
    shown_to_judge: Callable[[Any], str]
    answered: type[BaseModel]  # the record with its note
    results_only: str


TRIALS = Source(
    name="trials",
    kind="trial_search",
    answer_prompt=ANSWER_PROMPT,
    judge_prompt=TRIAL_JUDGE_PROMPT,
    # The LLM sees each trial's ID, title and status: not its site or distance.
    shown_to_writer=lambda trial: f"{trial.nct_id}: {trial.title} ({trial.overall_status})",
    shown_to_judge=lambda trial: f"Title: {trial.title}\nSummary: {trial.summary or 'none given'}",
    answered=AnsweredTrial,
    results_only=TRIALS_ONLY,
)
SOURCES = {TRIALS.name: TRIALS}


class State(TypedDict, total=False):
    question: str
    latitude: float
    longitude: float
    source: str
    condition: str
    records: list[Any]
    draft: Draft
    problems: list[str]
    attempts: int
    answer: Answer


class Agent:
    def __init__(self, model: BaseChatModel, judge: BaseChatModel) -> None:
        self.model = model
        self.judge = judge
        graph = StateGraph(State)
        # LLM calls are retried, with backoff, when the provider is briefly overloaded.
        llm_retry = RetryPolicy(max_attempts=3)
        graph.add_node("route", self.route, retry_policy=llm_retry)
        graph.add_node("find_trials", self.find_trials)
        graph.add_node("write_answer", self.write_answer, retry_policy=llm_retry)
        graph.add_node("check_output", self.check_output, retry_policy=llm_retry)
        graph.add_edge(START, "route")
        # Each gateway either sets the answer, which ends the run, or lets the run go on.
        graph.add_conditional_edges("route", after_route)
        graph.add_conditional_edges("find_trials", end_or("write_answer"))
        graph.add_edge("write_answer", "check_output")
        # A rejected draft goes back to write_answer, with the problems found.
        graph.add_conditional_edges("check_output", end_or("write_answer"))
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
    def route(self, state: State) -> State:
        """Gateway: searches go to their source's search; anything else gets a fixed refusal."""
        with tracer.start_as_current_span(
            "agent.route", record_exception=False, set_status_on_exception=False
        ) as span:
            reply = self.model.with_structured_output(Intake).invoke(
                [("system", INTAKE_PROMPT), ("human", state["question"])]
            )
            intake = Intake.model_validate(reply)
            span.set_attribute("agent.kind", intake.kind)
        for source in SOURCES.values():
            if intake.kind == source.kind and intake.condition:
                return {"source": source.name, "condition": intake.condition[:100]}
        kind = "other" if intake.kind not in REFUSALS else intake.kind
        return {"answer": Answer(outcome="refused", message=REFUSALS[kind])}

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
            return {"records": trials}
        message = f"I found no trials for {state['condition']} in the trial data."
        return {"answer": Answer(outcome="no_trials", message=message)}

    @DBOS.step()
    def write_answer(self, state: State) -> State:
        """The LLM sees each record numbered, as its source shows it to the writer."""
        source = SOURCES[state["source"]]
        with tracer.start_as_current_span(
            "agent.write_answer", record_exception=False, set_status_on_exception=False
        ) as span:
            lines = []
            for number, record in enumerate(state["records"], 1):
                lines.append(f"{number}. {source.shown_to_writer(record)}")
            records = "\n".join(lines)
            messages = [
                ("system", source.answer_prompt),
                ("human", f"Question: {state['question']}\n\n{source.name.title()}:\n{records}"),
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
        """Gateway: the draft must cite only the records found, give no doses or advice, and
        say nothing about a record that it doesn't (checked by the judge LLM)."""
        source = SOURCES[state["source"]]
        draft = state["draft"]
        records = state["records"]

        problems = []
        texts = [draft.summary]
        notes = {}
        for note in draft.notes:
            if not 1 <= note.number <= len(records):
                problems.append(f"{note.number} is not one of the {source.name} given")
            texts.append(note.note)
            notes[note.number] = note.note
        if any(DOSE.search(text) for text in texts):
            problems.append("it mentions a dose")
        if any(ADVICE.search(text) for text in texts):
            problems.append("it tells the user what to take")

        with tracer.start_as_current_span(
            "agent.check_output", record_exception=False, set_status_on_exception=False
        ) as span:
            # The judge is only asked once the cheap checks pass.
            unsupported = []
            if not problems and notes:
                unsupported = judge_notes(self.judge, source, records, notes)
            for number in unsupported:
                problems.append(f"the note on {number} says something its record doesn't")
            span.set_attribute("agent.passed", not problems)
            span.set_attribute("agent.problems", len(problems))
            span.set_attribute("agent.unsupported_notes", len(unsupported))

        if not problems:
            return {"answer": answer(source, "answered", draft.summary, records, notes)}
        # The problems quote model output, so only their number is logged.
        log.warning("answer failed %d checks", len(problems))
        if state["attempts"] < MAX_ANSWER_ATTEMPTS:
            return {"problems": problems}
        outcome = f"{source.name}_only"
        return {"answer": answer(source, outcome, source.results_only, records, {})}


@DBOS.workflow()
def answer_question(question: str, latitude: float, longitude: float) -> Answer:
    """`Agent.ask` as a durable run. DBOS saves the question, location and each step's result
    until `api.py` deletes the run, after RUN_TTL_MINUTES (60 by default).
    """
    return Agent(chat_model(), judge_model()).ask(question, latitude, longitude)


def judge_notes(
    judge: BaseChatModel, source: Source, records: list[Any], notes: dict[int, str]
) -> list[int]:
    """The numbers of the notes that the judge finds their record doesn't support."""
    shown = [source.shown_to_judge(record) for record in records]
    return unsupported_notes(judge, source.judge_prompt, shown, notes)


def answer(
    source: Source, outcome: str, message: str, records: list[Any], notes: dict[int, str]
) -> Answer:
    """The answer, with each record as found in the tables and the LLM's note on it, if any."""
    answered = []
    for number, record in enumerate(records, 1):
        answered.append(source.answered(**record.model_dump(), note=notes.get(number)))
    return Answer.model_validate({"outcome": outcome, "message": message, source.name: answered})


def after_route(state: State) -> str:
    return END if "answer" in state else f"find_{state['source']}"


def end_or(next_node: str) -> Callable[[State], str]:
    """A gateway's edge: the answer, once set, ends the run; otherwise the run goes on."""

    def route(state: State) -> str:
        return END if "answer" in state else next_node

    return route
