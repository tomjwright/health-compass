"""The grounding judge: an LLM that checks each note against the record it was written from.

The agent runs it on every answer, in `check_output`, and the evals run it again to score
groundedness. A note that claims something its record doesn't say is unsupported.
"""

from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel


class NoteVerdict(BaseModel):
    number: int
    supported: bool


class Verdicts(BaseModel):
    verdicts: list[NoteVerdict]


def unsupported_notes(
    judge: BaseChatModel, prompt: str, records: list[str], notes: dict[int, str]
) -> list[int]:
    """The numbers (from 1, in the records' order) of the notes that the judge finds their
    record doesn't support."""
    shown = []
    for number, record in enumerate(records, 1):
        if number in notes:
            shown.append(f"{number}\n{record}\nNote: {notes[number]}")
    messages = [("system", prompt), ("human", "\n\n".join(shown))]
    reply = judge.with_structured_output(Verdicts).invoke(messages)
    verdicts = Verdicts.model_validate(reply)
    supported = set()
    for verdict in verdicts.verdicts:
        if verdict.supported:
            supported.add(verdict.number)
    # A note the judge skipped counts as unsupported.
    unsupported = []
    for number in notes:
        if number not in supported:
            unsupported.append(number)
    return sorted(unsupported)
