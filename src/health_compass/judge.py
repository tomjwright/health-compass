"""The grounding judge: an LLM that checks each trial note against the trial's record.

The agent runs it on every answer, in `check_output`, and the evals run it again to score
groundedness. A note that claims something its record doesn't say is unsupported.
"""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel

from health_compass.trials import Trial

JUDGE_PROMPT = """You check notes written about clinical trials for the public.
Each trial comes with its record from ClinicalTrials.gov and a note about it. A note is
supported if everything it claims is stated in, or follows directly from, the record. Plain
paraphrase is fine. A note that adds a result, a benefit, or a detail that isn't in the record
is unsupported."""


class NoteVerdict(BaseModel):
    nct_id: str
    supported: bool


class Verdicts(BaseModel):
    verdicts: list[NoteVerdict]


def unsupported_notes(
    judge: BaseChatModel, trials: Sequence[Trial], notes: dict[str, str]
) -> list[str]:
    """The NCT IDs of the notes that the judge finds their trial's record doesn't support."""
    records = []
    for trial in trials:
        if trial.nct_id in notes:
            summary = trial.summary or "none given"
            note = notes[trial.nct_id]
            records.append(
                f"{trial.nct_id}\nTitle: {trial.title}\nSummary: {summary}\nNote: {note}"
            )
    messages = [("system", JUDGE_PROMPT), ("human", "\n\n".join(records))]
    reply = judge.with_structured_output(Verdicts).invoke(messages)
    verdicts = Verdicts.model_validate(reply)
    supported = set()
    for verdict in verdicts.verdicts:
        if verdict.supported:
            supported.add(verdict.nct_id)
    # A note the judge skipped counts as unsupported.
    unsupported = []
    for trial in trials:
        if trial.nct_id in notes and trial.nct_id not in supported:
            unsupported.append(trial.nct_id)
    return unsupported
