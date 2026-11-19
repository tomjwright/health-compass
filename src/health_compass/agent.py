"""The agent: one graph that answers "are there trials for X near me?" and "what's approved for
X?" from the tables.

          ┌─► find_trials ────┐
    route ┤                   ├─► write_answer ──► check_output ──► answered
      │   └─► find_approvals ─┘         ▲                │
      │          │                      └── fails once ──┤
      ▼          ▼                           fails twice ─┴──► trials_only / approvals_only
   refused   no_trials / no_approvals

`Supervisor.route` sorts the question and hands it to the agent for its kind, `TrialAgent` or
`ApprovalAgent`. Each agent is its own subgraph. A `RecordAgent` finds records, then writes and
checks one note per record with the shared `write_answer` and `check_output` steps; a subclass
sets only its search, prompts and how a record is shown.

The LLM only sorts the question and writes one note per record. Python decides what is refused
and what is shown, and every fact shown (status, site, distance) comes from the tables.
The question goes to the LLM; the location never does, and neither is logged or traced.
Approvals are from the US and the EU only, so every answer that lists them ends with
LOCAL_APPROVAL.

`answer_question` runs the agent as a durable DBOS workflow. Each node is a DBOS step, so after
a crash a run resumes from its last finished step, without calling the LLM again for the others.
"""

import logging
import os
import re
from collections.abc import Callable
from typing import Any, ClassVar, Literal, TypedDict

from dbos import DBOS
from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy
from opentelemetry import trace
from opentelemetry.trace import StatusCode
from pydantic import BaseModel, Field

from health_compass import research
from health_compass.approvals import (
    Approval,
    ApprovalSearch,
    ApprovalTablesNotBuiltError,
    find_approvals,
    indications_shown,
)
from health_compass.judge import unsupported_notes
from health_compass.trials import TablesNotBuiltError, Trial, TrialSearch, find_trials

log = logging.getLogger(__name__)
tracer = trace.get_tracer(__name__)

DEFAULT_MODEL = "google_genai:gemini-3.1-flash-lite"
MAX_TRIALS = 5
MAX_APPROVALS = 5
PAPERS_PER_MEDICINE = 2
# Abstracts are about 2,300 characters; a long one is cut here.
ABSTRACT_SHOWN = 3000
# Salts in US active-substance names, which papers leave out ("metformin", not "metformin
# hydrochloride").
SALT = re.compile(
    r"\s+(hydrochloride|hcl|sodium|potassium|calcium|phosphate|sulfate|maleate|mesylate|"
    r"citrate|tartrate|acetate|fumarate|succinate|besylate|hydrobromide|tosilate|tosylate|"
    r"benzoate|bromide|chloride|dipropionate|propionate|furoate|xinafoate)$",
    re.IGNORECASE,
)
MAX_ANSWER_ATTEMPTS = 2

REFUSALS = {
    "personal_medical_advice": (
        "I can't advise on doses or on which medicine you should take: please ask your "
        "doctor or pharmacist. I can find clinical trials for a condition near you, or list "
        "the medicines approved for it in the US and the EU."
    ),
    "other": (
        "I can only find clinical trials for a medical condition near you, or list the "
        'medicines approved for one in the US and the EU, for example "Are there alopecia '
        'trials near me?" or "What\'s approved for psoriasis?"'
    ),
}
TRIALS_ONLY = (
    "Here are the trials I found. I couldn't write a summary that passed my checks, "
    "so only the trial details are shown."
)
APPROVALS_ONLY = (
    "Here are the approvals I found. I couldn't write a summary that passed my checks, "
    "so only the approval details are shown."
)
LOCAL_APPROVAL = (
    "These approvals are from the US FDA and the European Medicines Agency. Approval differs by "
    "country: check with your doctor, pharmacist or local medicines regulator whether a "
    "medicine is approved where you live."
)

INTAKE_PROMPT = """Sort the user's question for a finder of clinical trials and approved medicines.
- trial_search: they want clinical trials for a medical condition.
- approval_search: they ask which medicines are approved for a medical condition.
- personal_medical_advice: they ask about doses, which medicine they should take, start or
  stop, or which medicine is best for them.
- other: anything else, including what a named medicine is approved for.
For a search, give the condition as its official MeSH heading, e.g. "Alopecia" for
"hair loss" or "Hypertension" for "high blood pressure". For approval_search, also give up to 3
other words for it that a US drug label might use, e.g. "high blood pressure"."""

ANSWER_PROMPT = """You describe clinical trials to a member of the public.
Use only the trials given. Never give doses or advice on what to take.
Write a one or two sentence summary that suggests discussing trials with their doctor, and one
plain-English sentence per trial on what it studies, citing it by its number."""

TRIAL_JUDGE_PROMPT = """You check notes written about clinical trials for the public.
Each trial comes with its record from ClinicalTrials.gov and a note about it. A note is
supported if everything it claims is stated in, or follows directly from, the record. Plain
paraphrase is fine. A note that adds a result, a benefit, or a detail that isn't in the record
is unsupported."""

APPROVAL_ANSWER_PROMPT = """You describe medicines approved in the US or the EU to a member of
the public. Use only the approvals given. Never give doses or advice on what to take, and never
say which medicine is best or compare how well they work. Write a one or two sentence summary
that suggests discussing treatment with their doctor, and one plain-English sentence per
approval on what it is approved for, citing it by its number."""

APPROVAL_JUDGE_PROMPT = """You check notes written about approved medicines for the public.
Each medicine comes with what its FDA label or EMA record says it is approved for, and a note
about it. A note is supported if everything it claims is stated in, or follows directly from,
the record. Plain paraphrase is fine. A note that adds a use, a benefit, a comparison or a
detail that isn't in the record is unsupported."""

DOSE = re.compile(r"\b\d+(\.\d+)?\s?(mg|mcg|µg|ml|iu|units?)\b", re.IGNORECASE)
ADVICE = re.compile(r"\byou should (take|start|stop|use)\b|\b(take|increase|reduce) your\b", re.I)
RESEARCH_ANSWER_PROMPT = """You describe published research on approved medicines to a member
of the public. Use only the papers given. Never give doses or advice on what to take, and never
say which medicine is best. Write a one or two sentence summary of what the papers studied,
without ranking the medicines against each other, and one plain-English sentence per paper on
what it found, citing it by its number. A sentence may compare a medicine with placebo or with
another treatment only as its paper does."""

RESEARCH_JUDGE_PROMPT = """You check notes written about medical research papers for the public.
Each paper comes with its title and abstract, and a note about it. A note is supported if
everything it claims, including any comparison, is stated in, or follows directly from, the
abstract. Plain paraphrase is fine. A note that adds a result, a benefit, a comparison or a
detail that isn't in the abstract is unsupported."""

RANKING = re.compile(
    r"\b(best|most effective|strongest|(better|safer|stronger|more effective) than|superior|"
    r"first[- ]line|preferred)\b",
    re.IGNORECASE,
)


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
    kind: Literal["trial_search", "approval_search", "personal_medical_advice", "other"]
    condition: str | None = Field(description="The condition's MeSH heading, or null.")
    terms: list[str] = Field(default=[], description="Up to 3 other words for the condition.")


class Note(BaseModel):
    number: int
    note: str = Field(description="One sentence on what the record is about.")


class Draft(BaseModel):
    summary: str
    notes: list[Note]


class AnsweredTrial(Trial):
    note: str | None


class AnsweredApproval(Approval):
    note: str | None


class MedicinePaper(research.Paper):
    medicine: str  # the approved medicine it was found for


class AnsweredPaper(MedicinePaper):
    note: str | None


class Answer(BaseModel):
    outcome: Literal[
        "answered", "refused", "no_trials", "trials_only", "no_approvals", "approvals_only"
    ]
    message: str
    trials: list[AnsweredTrial] = []
    approvals: list[AnsweredApproval] = []
    research: list[AnsweredPaper] = []


class State(TypedDict, total=False):
    question: str
    latitude: float
    longitude: float
    agent: str
    condition: str
    terms: list[str]
    records: list[Any]
    draft: Draft
    problems: list[str]
    attempts: int
    answer: Answer
    researched: bool


# LLM calls are retried, with backoff, when the provider is briefly overloaded.
LLM_RETRY = RetryPolicy(max_attempts=3)


class RecordAgent:
    """An agent that finds records, then writes and checks one note per record:

        find ──► write_answer ──► check_output ──► END
                      ▲                 │ fails once
                      └─────────────────┘

    A subclass sets what differs: its search (`find`), prompts and how a record is shown.
    """

    name: str  # the Answer field its records go in, e.g. "trials"
    kind: str  # the Intake kind the supervisor sends to it
    answer_prompt: str
    judge_prompt: str
    answered: type[BaseModel]  # a record with its note
    results_only: str  # the message when no draft passes the checks
    # Beyond the dose and advice checks: (pattern, the problem when the draft matches it).
    checks: tuple[tuple[re.Pattern[str], str], ...] = ()
    summary_checks: tuple[tuple[re.Pattern[str], str], ...] = ()  # the same, for the summary only
    closing: str = ""  # added to every answer that lists records

    def __init__(self, model: BaseChatModel, judge: BaseChatModel) -> None:
        self.model = model
        self.judge = judge

    def find(self, state: State) -> State:
        raise NotImplementedError

    def shown_to_writer(self, record: Any) -> str:
        raise NotImplementedError

    def shown_to_judge(self, record: Any) -> str:
        raise NotImplementedError

    def id_of(self, record: Any) -> str:
        raise NotImplementedError

    def done(self, state: State) -> bool:
        """Whether this agent's part of the run is over."""
        return "answer" in state

    def finish(self, state: State, answer: Answer) -> State:
        return {"answer": answer}

    def graph(self) -> Any:
        find = f"find_{self.name}"
        graph = StateGraph(State)
        graph.add_node(find, self.find)
        graph.add_node("write_answer", self.write_answer, retry_policy=LLM_RETRY)
        graph.add_node("check_output", self.check_output, retry_policy=LLM_RETRY)
        graph.add_edge(START, find)
        # Each gateway either finishes the agent's part of the run, or lets it go on. A rejected
        # draft goes back to write_answer, with the problems found.
        graph.add_conditional_edges(find, self.end_or_write)
        graph.add_edge("write_answer", "check_output")
        graph.add_conditional_edges("check_output", self.end_or_write)
        return graph.compile()

    def end_or_write(self, state: State) -> str:
        return END if self.done(state) else "write_answer"

    @DBOS.step()
    def write_answer(self, state: State) -> State:
        """The LLM sees each record numbered, as `shown_to_writer` shows it."""
        with tracer.start_as_current_span(
            "agent.write_answer", record_exception=False, set_status_on_exception=False
        ) as span:
            lines = []
            for number, record in enumerate(state["records"], 1):
                lines.append(f"{number}. {self.shown_to_writer(record)}")
            records = "\n".join(lines)
            messages = [
                ("system", self.answer_prompt),
                ("human", f"Question: {state['question']}\n\n{self.name.title()}:\n{records}"),
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
        draft = state["draft"]
        records = state["records"]

        problems = []
        texts = [draft.summary]
        notes = {}
        for note in draft.notes:
            if not 1 <= note.number <= len(records):
                problems.append(f"{note.number} is not one of the {self.name} given")
            texts.append(note.note)
            notes[note.number] = note.note
        if any(DOSE.search(text) for text in texts):
            problems.append("it mentions a dose")
        if any(ADVICE.search(text) for text in texts):
            problems.append("it tells the user what to take")
        for pattern, problem in self.checks:
            if any(pattern.search(text) for text in texts):
                problems.append(problem)
        for pattern, problem in self.summary_checks:
            if pattern.search(draft.summary):
                problems.append(problem)

        with tracer.start_as_current_span(
            "agent.check_output", record_exception=False, set_status_on_exception=False
        ) as span:
            # The judge is only asked once the cheap checks pass.
            unsupported = []
            if not problems and notes:
                unsupported = self.judge_notes(records, notes)
            for number in unsupported:
                problems.append(f"the note on {number} says something its record doesn't")
            span.set_attribute("agent.passed", not problems)
            span.set_attribute("agent.problems", len(problems))
            span.set_attribute("agent.unsupported_notes", len(unsupported))

        if not problems:
            return self.finish(state, self.answer("answered", draft.summary, records, notes))
        # The problems quote model output, so only their number is logged.
        log.warning("answer failed %d checks", len(problems))
        if state["attempts"] < MAX_ANSWER_ATTEMPTS:
            return {"problems": problems}
        return self.finish(state, self.answer(f"{self.name}_only", self.results_only, records, {}))

    def judge_notes(self, records: list[Any], notes: dict[int, str]) -> list[int]:
        """The numbers of the notes that the judge finds their record doesn't support."""
        shown = [self.shown_to_judge(record) for record in records]
        return unsupported_notes(self.judge, self.judge_prompt, shown, notes)

    def answer(
        self, outcome: str, message: str, records: list[Any], notes: dict[int, str]
    ) -> Answer:
        """The answer, with each record as found in the tables and the LLM's note on it."""
        answered = []
        for number, record in enumerate(records, 1):
            answered.append(self.answered(**record.model_dump(), note=notes.get(number)))
        if self.closing:
            message = f"{message}\n\n{self.closing}"
        return Answer.model_validate({"outcome": outcome, "message": message, self.name: answered})


class TrialAgent(RecordAgent):
    """Trials for a condition near the user."""

    name = "trials"
    kind = "trial_search"
    answer_prompt = ANSWER_PROMPT
    judge_prompt = TRIAL_JUDGE_PROMPT
    answered = AnsweredTrial
    results_only = TRIALS_ONLY

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

    find = find_trials

    def shown_to_writer(self, trial: Trial) -> str:
        # The LLM sees each trial's ID, title and status: not its site or distance.
        return f"{trial.nct_id}: {trial.title} ({trial.overall_status})"

    def shown_to_judge(self, trial: Trial) -> str:
        return f"Title: {trial.title}\nSummary: {trial.summary or 'none given'}"

    def id_of(self, trial: Trial) -> str:
        return trial.nct_id


class ApprovalAgent(RecordAgent):
    """Medicines approved now in the US and the EU for a condition. It never uses the location,
    so every answer that lists approvals says that approval differs by country."""

    name = "approvals"
    kind = "approval_search"
    answer_prompt = APPROVAL_ANSWER_PROMPT
    judge_prompt = APPROVAL_JUDGE_PROMPT
    answered = AnsweredApproval
    results_only = APPROVALS_ONLY
    checks = ((RANKING, "it ranks the medicines"),)
    closing = LOCAL_APPROVAL

    @DBOS.step()
    def find_approvals(self, state: State) -> State:
        with tracer.start_as_current_span("agent.find_approvals") as span:
            search = ApprovalSearch(
                condition=state["condition"], terms=state["terms"], limit=MAX_APPROVALS
            )
            approvals = find_approvals(search).approvals
            span.set_attribute("agent.approvals_found", len(approvals))
        if approvals:
            return {"records": approvals}
        message = (
            f"I found no medicines approved now for {state['condition']} by the US FDA or the "
            "European Medicines Agency. Approval differs by country: check with your doctor, "
            "pharmacist or local medicines regulator."
        )
        return {"answer": Answer(outcome="no_approvals", message=message)}

    find = find_approvals

    def shown_to_writer(self, approval: Approval) -> str:
        return (
            f"{approval.approval_id}: {approval.medicine_name} ({approval.active_substance})\n"
            f"{indications_shown(approval)}"
        )

    def shown_to_judge(self, approval: Approval) -> str:
        return (
            f"Medicine: {approval.medicine_name} ({approval.active_substance})\n"
            f"Approved for: {indications_shown(approval)}"
        )

    def id_of(self, approval: Approval) -> str:
        return approval.approval_id


class ResearchAgent(RecordAgent):
    """The newest reviews and randomised trials of each approved medicine for the condition. It
    runs after ApprovalAgent and adds to its answer; with no papers, the answer is unchanged."""

    name = "research"
    kind = ""  # the supervisor never routes to it: it follows ApprovalAgent
    answer_prompt = RESEARCH_ANSWER_PROMPT
    judge_prompt = RESEARCH_JUDGE_PROMPT
    answered = AnsweredPaper
    results_only = ""
    # A note may compare as its paper does (the judge checks that); the summary may not rank.
    summary_checks = ((RANKING, "it ranks the medicines"),)

    def __init__(
        self,
        model: BaseChatModel,
        judge: BaseChatModel,
        find_papers: Callable[[research.PaperSearch], research.PaperResults],
    ) -> None:
        super().__init__(model, judge)
        self.find_papers = find_papers

    @DBOS.step()
    def find_research(self, state: State) -> State:
        answer = state["answer"]
        papers: dict[str, MedicinePaper] = {}
        if answer.outcome in ("answered", "approvals_only"):
            with tracer.start_as_current_span("agent.find_research") as span:
                for medicine in medicines(answer.approvals):
                    search = research.PaperSearch(
                        medicine=medicine,
                        condition=state["condition"],
                        terms=state["terms"],
                        limit=PAPERS_PER_MEDICINE,
                    )
                    try:
                        found = self.find_papers(search).papers
                    except research.ResearchUnavailableError:
                        log.warning("Europe PMC couldn't be reached")
                        break
                    for paper in found:
                        papers.setdefault(
                            paper.pmid, MedicinePaper(**paper.model_dump(), medicine=medicine)
                        )
                span.set_attribute("agent.papers_found", len(papers))
        if not papers:
            return {"researched": True}
        return {"records": list(papers.values()), "attempts": 0, "problems": []}

    find = find_research

    def shown_to_writer(self, paper: MedicinePaper) -> str:
        year = paper.published_on.year if paper.published_on else "undated"
        return (
            f"{paper.medicine}: {paper.title} ({paper.paper_type}, {year})\n"
            f"{paper.abstract[:ABSTRACT_SHOWN]}"
        )

    def shown_to_judge(self, paper: MedicinePaper) -> str:
        return f"Title: {paper.title}\nAbstract: {paper.abstract[:ABSTRACT_SHOWN]}"

    def id_of(self, paper: MedicinePaper) -> str:
        return paper.pmid

    def done(self, state: State) -> bool:
        return state.get("researched", False)

    def answer(
        self, outcome: str, message: str, records: list[Any], notes: dict[int, str]
    ) -> Answer:
        """Just the papers, with their notes; `finish` adds them to the approvals answer."""
        answered = []
        for number, record in enumerate(records, 1):
            answered.append(AnsweredPaper(**record.model_dump(), note=notes.get(number)))
        return Answer(outcome="answered", message=message, research=answered)

    def finish(self, state: State, answer: Answer) -> State:
        """The research summary goes before the approvals' closing note, which stays last."""
        approvals = state["answer"]
        message = approvals.message.removesuffix(f"\n\n{LOCAL_APPROVAL}")
        if answer.message:
            message = f"{message}\n\n{answer.message}"
        message = f"{message}\n\n{LOCAL_APPROVAL}"
        update = {"message": message, "research": answer.research}
        return {"answer": approvals.model_copy(update=update), "researched": True}


class Supervisor:
    """Sorts the question, then hands it to the agent for its kind, or refuses it:

    route ──► trials    (TrialAgent's graph) ───────────────────────────────────┐
      ├─────► approvals (ApprovalAgent's graph) ─► research (ResearchAgent's graph) ─┴──► END
      └─────► refused
    """

    # Agents that run after another, adding to its answer.
    FOLLOWED_BY: ClassVar = {"approvals": "research"}

    def __init__(
        self,
        model: BaseChatModel,
        judge: BaseChatModel,
        find_papers: Callable[[research.PaperSearch], research.PaperResults] = (
            research.find_papers
        ),
    ) -> None:
        self.model = model
        agents: list[RecordAgent] = [
            TrialAgent(model, judge),
            ApprovalAgent(model, judge),
            ResearchAgent(model, judge, find_papers),
        ]
        self.agents = {agent.name: agent for agent in agents}
        graph = StateGraph(State)
        graph.add_node("route", self.route, retry_policy=LLM_RETRY)
        for name, agent in self.agents.items():
            graph.add_node(name, agent.graph())
            graph.add_edge(name, self.FOLLOWED_BY.get(name, END))
        graph.add_edge(START, "route")
        graph.add_conditional_edges("route", after_route)
        self.graph = graph.compile()

    def ask(self, question: str, latitude: float, longitude: float) -> Answer:
        with tracer.start_as_current_span(
            "agent.ask", record_exception=False, set_status_on_exception=False
        ) as span:
            try:
                state = self.graph.invoke(
                    {"question": question, "latitude": latitude, "longitude": longitude}
                )
            except (TablesNotBuiltError, ApprovalTablesNotBuiltError):
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
        """Gateway: a search goes to the agent for its kind; anything else gets a refusal."""
        with tracer.start_as_current_span(
            "agent.route", record_exception=False, set_status_on_exception=False
        ) as span:
            reply = self.model.with_structured_output(Intake).invoke(
                [("system", INTAKE_PROMPT), ("human", state["question"])]
            )
            intake = Intake.model_validate(reply)
            span.set_attribute("agent.kind", intake.kind)
        for agent in self.agents.values():
            if intake.kind == agent.kind and intake.condition:
                terms = [term.strip()[:100] for term in intake.terms if len(term.strip()) >= 2]
                return {
                    "agent": agent.name,
                    "condition": intake.condition[:100],
                    "terms": terms[:3],
                }
        kind = "other" if intake.kind not in REFUSALS else intake.kind
        return {"answer": Answer(outcome="refused", message=REFUSALS[kind])}


@DBOS.workflow()
def answer_question(question: str, latitude: float, longitude: float) -> Answer:
    """`Supervisor.ask` as a durable run. DBOS saves the question, location and each step's result
    until `api.py` deletes the run, after RUN_TTL_MINUTES (60 by default).
    """
    return Supervisor(chat_model(), judge_model()).ask(question, latitude, longitude)


def after_route(state: State) -> str:
    return END if "answer" in state else state["agent"]


def medicines(approvals: list[AnsweredApproval]) -> list[str]:
    """Each approval's active substance, once, as papers name it: without its salt, and only the
    first of a combination."""
    names: list[str] = []
    for approval in approvals:
        if not approval.active_substance:
            continue
        first = re.split(r"[;,/]| and ", approval.active_substance)[0].strip().lower()
        # FDA adds a four-letter suffix to biologics' names, e.g. "omalizumab-igec".
        name = SALT.sub("", re.sub(r"-[a-z]{4}$", "", first))[:100]
        if len(name) >= 2 and name not in names:
            names.append(name)
    return names
