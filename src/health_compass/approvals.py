"""Find the medicines approved for a condition in the US (FDA) and the EU (EMA).

The search itself is plain SQL in find_approvals.sql.
"""

import logging
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import duckdb
from pydantic import BaseModel, Field

from health_compass.transform import warehouse_path

log = logging.getLogger(__name__)

FIND_APPROVALS = (Path(__file__).parent / "find_approvals.sql").read_text()
# Labels can run to 23,000 characters; what a medicine is approved for comes first.
INDICATIONS_SHOWN = 2000

Term = Annotated[str, Field(min_length=2, max_length=100)]


class ApprovalTablesNotBuiltError(Exception):
    """The approval tables haven't been built yet: load openFDA and EMA, then run the transform."""


class ApprovalSearch(BaseModel):
    """A condition. Sent as a POST body, so it stays out of URLs and traces."""

    condition: Term = Field(
        description="The condition's MeSH heading, matched to EU approvals.",
        examples=["Diabetes Mellitus, Type 2"],
    )
    terms: list[Term] = Field(
        default=[],
        max_length=3,
        description="Other words for it, matched to US label text along with the condition.",
        examples=[["type 2 diabetes"]],
    )
    limit: int = Field(default=20, ge=1, le=100)


class Approval(BaseModel):
    """A medicine approved now: EU status "Authorised" or US status "Marketed"."""

    region: Literal["US", "EU"]
    approval_id: str
    medicine_name: str | None
    active_substance: str | None
    holder: str | None
    status: str
    approved_on: date | None
    is_conditional: bool | None
    is_orphan: bool | None
    indications: str | None
    source_url: str | None


class ApprovalResults(BaseModel):
    approvals: list[Approval]


def find_approvals(search: ApprovalSearch) -> ApprovalResults:
    """Medicines approved now for a condition, newest approval first."""
    database = warehouse_path()
    if not database.exists():
        raise ApprovalTablesNotBuiltError
    params = {
        "condition": search.condition,
        "terms": [search.condition, *search.terms],
        "limit": search.limit,
    }
    # A new read-only connection per request, so each request reads the latest build.
    with duckdb.connect(str(database), read_only=True) as db:
        try:
            rows = db.execute(FIND_APPROVALS, params).fetchall()
        except duckdb.CatalogException:
            # The transform skips the approval tables until openFDA and EMA are loaded.
            raise ApprovalTablesNotBuiltError from None
        names = [column[0] for column in db.description]
    log.info("found %d approvals", len(rows))
    approvals = [Approval(**dict(zip(names, row, strict=True))) for row in rows]
    return ApprovalResults(approvals=approvals)


def indications_shown(approval: Approval) -> str:
    """The start of what the approval says the medicine is for: what the LLM and judge see."""
    return (approval.indications or "none given")[:INDICATIONS_SHOWN]
