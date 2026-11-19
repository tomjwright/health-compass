"""The grounding judge: an LLM that checks each note against the record it was written from.

The agents run it on every answer, in `check_output`, and the evals run it again to score
groundedness. A note that claims something its record doesn't say is unsupported.
"""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from health_compass.approvals import Approval, indications_shown
from health_compass.trials import Trial

TRIAL_PROMPT = """You check notes written about clinical trials for the public.
Each trial comes with its record from ClinicalTrials.gov and a note about it. A note is
supported if everything it claims is stated in, or follows directly from, the record. Plain
paraphrase is fine. A note that adds a result, a benefit, or a detail that isn't in the record
is unsupported."""

APPROVAL_PROMPT = """You check notes written about approved medicines for the public.
Each medicine comes with what its FDA label or EMA record says it is approved for, and a note
about it. A note is supported if everything it claims is stated in, or follows directly from,
the record. Plain paraphrase is fine. A note that adds a use, a benefit, a comparison or a
detail that isn't in the record is unsupported."""


class NoteVerdict(BaseModel):
    id: str
    supported: bool


class Verdicts(BaseModel):
    verdicts: list[NoteVerdict]


def unsupported_notes(
    judge: BaseChatModel, trials: Sequence[Trial], notes: dict[str, str]
) -> list[str]:
    """The NCT IDs of the notes that the judge finds their trial's record doesn't support."""
    records = {}
    for trial in trials:
        records[trial.nct_id] = f"Title: {trial.title}\nSummary: {trial.summary or 'none given'}"
    return unsupported(judge, TRIAL_PROMPT, records, notes)


def unsupported_approval_notes(
    judge: BaseChatModel, approvals: Sequence[Approval], notes: dict[str, str]
) -> list[str]:
    """The approval IDs of the notes that the judge finds their approval doesn't support."""
    records = {}
    for approval in approvals:
        medicine = f"{approval.medicine_name} ({approval.active_substance})"
        records[approval.approval_id] = (
            f"Medicine: {medicine}\nApproved for: {indications_shown(approval)}"
        )
    return unsupported(judge, APPROVAL_PROMPT, records, notes)


def unsupported(
    judge: BaseChatModel, prompt: str, records: dict[str, str], notes: dict[str, str]
) -> list[str]:
    """The IDs of the notes that the judge finds their record doesn't support."""
    shown = []
    for record_id, record in records.items():
        if record_id in notes:
            shown.append(f"{record_id}\n{record}\nNote: {notes[record_id]}")
    messages = [("system", prompt), ("human", "\n\n".join(shown))]
    reply = judge.with_structured_output(Verdicts).invoke(messages)
    verdicts = Verdicts.model_validate(reply)
    supported = set()
    for verdict in verdicts.verdicts:
        if verdict.supported:
            supported.add(verdict.id)
    # A note the judge skipped counts as unsupported.
    unsupported = []
    for record_id in records:
        if record_id in notes and record_id not in supported:
            unsupported.append(record_id)
    return unsupported
