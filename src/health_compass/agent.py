"""The first agent: answers "are there trials for X near me?" from the trial tables.

    check_input ──► find_trials ──► write_answer ──► check_output ──► answered
         │               │               ▲                 │
         ▼               ▼               └── fails once ───┤
      refused        no_trials                fails twice ─┴──► trials_only

The LLM only sorts the question and writes one note per trial. Python decides what is refused
and what is shown, and every fact shown (status, site, distance) comes from the tables.
The question goes to the LLM; the location never does, and neither is logged or traced.
"""

import logging
import os
import re
from typing import Literal, TypedDict

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from opentelemetry import trace
from pydantic import BaseModel, Field

from health_compass.trials import Trial, TrialSearch, find_trials

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
For trial_search, give the condition by its usual medical name, e.g. "alopecia" for
"hair loss"."""

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


class AgentError(Exception):
    """The agent failed. It carries no model output, so it is safe to log and trace."""


class Intake(BaseModel):
    kind: Literal["trial_search", "personal_medical_advice", "other"]
    condition: str | None = Field(description="The condition's medical name, or null.")


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
    def __init__(self, model: BaseChatModel) -> None:
        self.model = model
        graph = StateGraph(State)
        # LLM calls are retried, with backoff, when the provider is briefly overloaded.
        llm_retry = RetryPolicy(max_attempts=3)
        graph.add_node("check_input", self.check_input, retry_policy=llm_retry)
        graph.add_node("find_trials", self.find_trials)
        graph.add_node("write_answer", self.write_answer, retry_policy=llm_retry)
        graph.add_node("check_output", self.check_output)
        graph.add_edge(START, "check_input")
        # Each gateway either sets the answer, which ends the run, or lets the run go on.
        graph.add_conditional_edges("check_input", after_check_input)
        graph.add_conditional_edges("find_trials", after_find_trials)
        graph.add_edge("write_answer", "check_output")
        graph.add_conditional_edges("check_output", after_check_output)
        self.graph = graph.compile()

    def ask(self, question: str, latitude: float, longitude: float) -> Answer:
        with tracer.start_as_current_span("agent.ask", record_exception=False) as span:
            try:
                state = self.graph.invoke(
                    {"question": question, "latitude": latitude, "longitude": longitude}
                )
            except Exception as error:
                # LLM errors can quote the model's output, which can repeat the question, so
                # only the error's type reaches traces and logs.
                raise AgentError(f"the agent failed with {type(error).__name__}") from None
            answer: Answer = state["answer"]
            span.set_attribute("agent.outcome", answer.outcome)
        log.info("agent outcome: %s", answer.outcome)
        return answer

    def check_input(self, state: State) -> State:
        """Gateway: only trial searches go on; anything else gets a fixed refusal."""
        with tracer.start_as_current_span("agent.check_input", record_exception=False) as span:
            reply = self.model.with_structured_output(Intake).invoke(
                [("system", INTAKE_PROMPT), ("human", state["question"])]
            )
            intake = Intake.model_validate(reply)
            span.set_attribute("agent.kind", intake.kind)
        if intake.kind == "trial_search" and intake.condition:
            return {"condition": intake.condition[:100]}
        kind = "other" if intake.kind == "trial_search" else intake.kind
        return {"answer": Answer(outcome="refused", message=REFUSALS[kind], trials=[])}

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
            return {"trials": trials}
        message = f"I found no trials for {state['condition']} in the trial data."
        return {"answer": Answer(outcome="no_trials", message=message, trials=[])}

    def write_answer(self, state: State) -> State:
        """The LLM sees each trial's ID, title and status: not its site or distance."""
        with tracer.start_as_current_span("agent.write_answer", record_exception=False) as span:
            trials = "\n".join(
                f"{t.nct_id}: {t.title} ({t.overall_status})" for t in state["trials"]
            )
            messages = [
                ("system", ANSWER_PROMPT),
                ("human", f"Question: {state['question']}\n\nTrials:\n{trials}"),
            ]
            if state.get("problems"):
                problems = "; ".join(state["problems"])
                messages.append(("human", f"Your last answer was rejected: {problems}. Redo it."))
            reply = self.model.with_structured_output(Draft).invoke(messages)
            attempts = state.get("attempts", 0) + 1
            span.set_attribute("agent.attempt", attempts)
        return {"draft": Draft.model_validate(reply), "attempts": attempts}

    def check_output(self, state: State) -> State:
        """Gateway: the draft must cite only the trials found, and give no doses or advice."""
        draft, trials = state["draft"], state["trials"]
        found = {t.nct_id for t in trials}
        problems = [
            f"{n.nct_id} is not one of the trials given"
            for n in draft.notes
            if n.nct_id not in found
        ]
        texts = [draft.summary, *(n.note for n in draft.notes)]
        if any(DOSE.search(text) for text in texts):
            problems.append("it mentions a dose")
        if any(ADVICE.search(text) for text in texts):
            problems.append("it tells the user what to take")
        with tracer.start_as_current_span("agent.check_output") as span:
            span.set_attribute("agent.passed", not problems)
            span.set_attribute("agent.problems", len(problems))
        notes = {n.nct_id: n.note for n in draft.notes}
        if not problems:
            answered = [AnsweredTrial(**t.model_dump(), note=notes.get(t.nct_id)) for t in trials]
            return {"answer": Answer(outcome="answered", message=draft.summary, trials=answered)}
        log.warning("answer failed its checks: %s", "; ".join(problems))
        if state["attempts"] < MAX_ANSWER_ATTEMPTS:
            return {"problems": problems}
        bare = [AnsweredTrial(**t.model_dump(), note=None) for t in trials]
        return {"answer": Answer(outcome="trials_only", message=TRIALS_ONLY, trials=bare)}


def after_check_input(state: State) -> str:
    return END if "answer" in state else "find_trials"


def after_find_trials(state: State) -> str:
    return END if "answer" in state else "write_answer"


def after_check_output(state: State) -> str:
    """A rejected draft goes back to write_answer, with the problems found."""
    return END if "answer" in state else "write_answer"
