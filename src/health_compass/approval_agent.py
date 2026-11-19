"""The approvals agent: answers "what's approved for X?" from the approval tables.

    check_input ──► find_approvals ──► write_answer ──► check_output ──► answered
         │                │                 ▲                 │
         ▼                ▼                 └── fails once ───┤
      refused        no_approvals               fails twice ──┴──► approvals_only

Like the trial agent, the LLM only sorts the question and writes one note per approval. Python
decides what is refused and what is shown, and every fact shown (region, status, date, link)
comes from the tables. The approvals are from the US and the EU only, so every answer that lists
them ends with LOCAL_APPROVAL. The agent never sees the user's location.
"""

import logging
import re
from collections.abc import Callable
from typing import Literal, TypedDict

from dbos import DBOS
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, Field

from health_compass.agent import ADVICE, DOSE, MAX_ANSWER_ATTEMPTS, AgentError
from health_compass.approvals import (
    Approval,
    ApprovalSearch,
    ApprovalTablesNotBuiltError,
    find_approvals,
    indications_shown,
)
from health_compass.judge import unsupported_approval_notes

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

MAX_APPROVALS = 5

REFUSALS = {
    "personal_medical_advice": (
        "I can't advise on doses or on which medicine you should take: please ask your doctor "
        "or pharmacist. I can list the medicines approved for a condition in the US and the EU."
    ),
    "other": (
        "I can only list the medicines approved for a medical condition in the US and the EU, "
        'for example "What\'s approved for psoriasis?"'
    ),
}
LOCAL_APPROVAL = (
    "These approvals are from the US FDA and the European Medicines Agency. Approval differs by "
    "country: check with your doctor, pharmacist or local medicines regulator whether a "
    "medicine is approved where you live."
)
APPROVALS_ONLY = (
    "Here are the approvals I found. I couldn't write a summary that passed my checks, "
    "so only the approval details are shown."
)

INTAKE_PROMPT = """Sort the user's question for a finder of approved medicines.
- approval_search: they ask which medicines are approved for a medical condition.
- personal_medical_advice: they ask about doses, which medicine they should take, start or
  stop, or which medicine is best for them.
- other: anything else, including what a named medicine is approved for.
For approval_search, give the condition as its official MeSH heading, e.g. "Hypertension" for
"high blood pressure", and up to 3 other words for it that a US drug label might use, e.g.
"high blood pressure"."""

ANSWER_PROMPT = """You describe medicines approved in the US or the EU to a member of the public.
Use only the approvals given. Never give doses or advice on what to take, and never say which
medicine is best or compare how well they work. Write a one or two sentence summary that
suggests discussing treatment with their doctor, and one plain-English sentence per approval
on what it is approved for, using its approval ID exactly as given."""

RANKING = re.compile(r"\b(best|most effective|strongest)\b", re.IGNORECASE)


class Intake(BaseModel):
    kind: Literal["approval_search", "personal_medical_advice", "other"]
    condition: str | None = Field(description="The condition's MeSH heading, or null.")
    terms: list[str] = Field(default=[], description="Up to 3 other words for the condition.")


class ApprovalNote(BaseModel):
    approval_id: str
    note: str = Field(description="One sentence on what the medicine is approved for.")


class Draft(BaseModel):
    summary: str
    notes: list[ApprovalNote]


class AnsweredApproval(Approval):
    note: str | None


class ApprovalAnswer(BaseModel):
    outcome: Literal["answered", "refused", "no_approvals", "approvals_only"]
    message: str
    approvals: list[AnsweredApproval]


class State(TypedDict, total=False):
    question: str
    search: ApprovalSearch
    approvals: list[Approval]
    draft: Draft
    problems: list[str]
    attempts: int
    answer: ApprovalAnswer


class ApprovalAgent:
    def __init__(self, model: BaseChatModel, judge: BaseChatModel) -> None:
        self.model = model
        self.judge = judge
        graph = StateGraph(State)
        llm_retry = RetryPolicy(max_attempts=3)
        graph.add_node("check_input", self.check_input, retry_policy=llm_retry)
        graph.add_node("find_approvals", self.find_approvals)
        graph.add_node("write_answer", self.write_answer, retry_policy=llm_retry)
        graph.add_node("check_output", self.check_output, retry_policy=llm_retry)
        graph.add_edge(START, "check_input")
        graph.add_conditional_edges("check_input", end_or("find_approvals"))
        graph.add_conditional_edges("find_approvals", end_or("write_answer"))
        graph.add_edge("write_answer", "check_output")
        graph.add_conditional_edges("check_output", end_or("write_answer"))
        self.graph = graph.compile()

    def ask(self, question: str) -> ApprovalAnswer:
        with tracer.start_as_current_span(
            "approval_agent.ask", record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                state = self.graph.invoke({"question": question})
            except ApprovalTablesNotBuiltError:
                raise
            except Exception as error:
                # As in the trial agent, only the error's type reaches traces and logs.
                span.set_status(StatusCode.ERROR, type(error).__name__)
                raise AgentError(f"the agent failed with {type(error).__name__}") from None
            answer: ApprovalAnswer = state["answer"]
            span.set_attribute("agent.outcome", answer.outcome)
        log.info("approval agent outcome: %s", answer.outcome)
        return answer

    @DBOS.step()
    def check_input(self, state: State) -> State:
        """Gateway: only searches by condition go on; anything else gets a fixed refusal."""
        reply = self.model.with_structured_output(Intake).invoke(
            [("system", INTAKE_PROMPT), ("human", state["question"])]
        )
        intake = Intake.model_validate(reply)
        condition = (intake.condition or "").strip()[:100]
        if intake.kind == "approval_search" and len(condition) >= 2:
            terms = [term.strip()[:100] for term in intake.terms if len(term.strip()) >= 2]
            search = ApprovalSearch(condition=condition, terms=terms[:3], limit=MAX_APPROVALS)
            return {"search": search}
        kind = "other" if intake.kind == "approval_search" else intake.kind
        return {"answer": ApprovalAnswer(outcome="refused", message=REFUSALS[kind], approvals=[])}

    @DBOS.step()
    def find_approvals(self, state: State) -> State:
        search = state["search"]
        approvals = find_approvals(search).approvals
        if approvals:
            return {"approvals": approvals}
        message = (
            f"I found no medicines approved now for {search.condition} by the US FDA or the "
            "European Medicines Agency. Approval differs by country: check with your doctor, "
            "pharmacist or local medicines regulator."
        )
        return {"answer": ApprovalAnswer(outcome="no_approvals", message=message, approvals=[])}

    @DBOS.step()
    def write_answer(self, state: State) -> State:
        """The LLM sees each approval's ID, medicine and the start of its indications."""
        lines = []
        for approval in state["approvals"]:
            medicine = f"{approval.medicine_name} ({approval.active_substance})"
            lines.append(f"{approval.approval_id}: {medicine}\n{indications_shown(approval)}")
        approvals = "\n\n".join(lines)
        messages = [
            ("system", ANSWER_PROMPT),
            ("human", f"Question: {state['question']}\n\nApprovals:\n{approvals}"),
        ]
        if state.get("problems"):
            problems = "; ".join(state["problems"])
            messages.append(("human", f"Your last answer was rejected: {problems}. Redo it."))
        reply = self.model.with_structured_output(Draft).invoke(messages)
        return {"draft": Draft.model_validate(reply), "attempts": state.get("attempts", 0) + 1}

    @DBOS.step()
    def check_output(self, state: State) -> State:
        """Gateway: the draft must cite only the approvals found, give no doses or advice, rank
        no medicine, and say nothing its approval doesn't (checked by the judge LLM)."""
        draft = state["draft"]
        approvals = state["approvals"]
        found = {approval.approval_id for approval in approvals}

        problems = []
        texts = [draft.summary]
        notes = {}
        for approval_note in draft.notes:
            if approval_note.approval_id not in found:
                problems.append(f"{approval_note.approval_id} is not one of the approvals given")
            texts.append(approval_note.note)
            notes[approval_note.approval_id] = approval_note.note
        if any(DOSE.search(text) for text in texts):
            problems.append("it mentions a dose")
        if any(ADVICE.search(text) for text in texts):
            problems.append("it tells the user what to take")
        if any(RANKING.search(text) for text in texts):
            problems.append("it ranks the medicines")
        # The judge is only asked once the cheap checks pass.
        if not problems and notes:
            for approval_id in unsupported_approval_notes(self.judge, approvals, notes):
                problems.append(f"the note on {approval_id} says something its record doesn't")

        if not problems:
            message = f"{draft.summary}\n\n{LOCAL_APPROVAL}"
            answered = add_notes(approvals, notes)
            answer = ApprovalAnswer(outcome="answered", message=message, approvals=answered)
            return {"answer": answer}
        log.warning("answer failed %d checks", len(problems))
        if state["attempts"] < MAX_ANSWER_ATTEMPTS:
            return {"problems": problems}
        message = f"{APPROVALS_ONLY}\n\n{LOCAL_APPROVAL}"
        bare = add_notes(approvals, {})
        return {"answer": ApprovalAnswer(outcome="approvals_only", message=message, approvals=bare)}


def add_notes(approvals: list[Approval], notes: dict[str, str]) -> list[AnsweredApproval]:
    """Each approval as found in the tables, with the LLM's note on it, if any."""
    answered = []
    for approval in approvals:
        note = notes.get(approval.approval_id)
        answered.append(AnsweredApproval(**approval.model_dump(), note=note))
    return answered


def end_or(next_node: str) -> Callable[[State], str]:
    """A gateway's edge: each gateway either sets the answer, which ends the run, or goes on."""

    def route(state: State) -> str:
        return END if "answer" in state else next_node

    return route
