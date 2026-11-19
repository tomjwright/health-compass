from pathlib import Path
from typing import Any

import dlt
import pytest
from fastapi.testclient import TestClient
from test_agent import FakeModel
from test_transform import FULL_STUDY, save_load

from health_compass import agent, approvals, transform
from health_compass.agent import Intake
from health_compass.api import app
from health_compass.ingest import common


@pytest.fixture(autouse=True)
def dirs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("DUCKDB_PATH", str(tmp_path / "health_compass.duckdb"))


def us_approval(number: str, brand: str, approved: str, indications: str) -> dict[str, Any]:
    """An FDA application with one marketed brand, and its label."""
    application = {
        "application_number": number,
        "sponsor_name": "PHARMA",
        "products": [
            {
                "brand_name": brand,
                "marketing_status": "Prescription",
                "active_ingredients": [{"name": "SOMETHING"}],
            }
        ],
        "submissions": [
            {
                "submission_type": "ORIG",
                "submission_status": "AP",
                "submission_status_date": approved,
            }
        ],
    }
    label = {
        "set_id": number,
        "effective_time": "20250101",
        "indications_and_usage": [indications],
        "openfda": {"application_number": [number], "brand_name": [brand]},
    }
    return {"application": application, "label": label}


def eu_approval(number: str, name: str, status: str, authorised: str, areas: str) -> dict[str, Any]:
    return {
        "ema_product_number": number,
        "name_of_medicine": name,
        "active_substance": "Metformin Hydrochloride AND Sitagliptin Phosphate",
        "medicine_status": status,
        "marketing_authorisation_date": authorised,
        "conditional_approval": "No",
        "orphan_medicine": "No",
        "therapeutic_area_mesh": areas,
        "therapeutic_indication": f"Treatment of {areas}",
        "marketing_authorisation_developer_applicant_holder": "Pharma Ltd",
        "medicine_url": f"https://www.ema.europa.eu/{name}",
    }


def build_approvals(us: list[dict[str, Any]], eu: list[dict[str, Any]]) -> None:
    applications = [approval["application"] for approval in us]
    labels = [approval["label"] for approval in us]
    common.pipeline("openfda").run(
        [
            dlt.resource(applications, name="applications", write_disposition="replace"),
            dlt.resource(labels, name="labels", write_disposition="replace"),
        ]
    )
    common.pipeline("ema").run(dlt.resource(eu, name="medicines", write_disposition="replace"))
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()


def test_finds_approvals_in_force_for_a_condition_newest_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    us = [
        us_approval("NDA000001", "OLDBRAND", "19990101", "For type 2 diabetes mellitus in adults."),
        us_approval(
            "BLA000002", "NEWBRAND", "20240601", "Improves blood sugar in Type 2 Diabetes."
        ),
        # A generic: it copies a brand's label, so it isn't listed.
        us_approval("ANDA000003", "GENERIC", "20250101", "For type 2 diabetes mellitus."),
        us_approval("NDA000004", "OTHER", "20230101", "For hypertension."),
    ]
    eu = [
        eu_approval(
            "EMEA/H/C/1", "Eudrug", "Authorised", "09/01/2017", "Diabetes Mellitus, Type 2"
        ),
        eu_approval(
            "EMEA/H/C/2", "Gonedrug", "Withdrawn", "09/01/2020", "Diabetes Mellitus, Type 2"
        ),
        eu_approval("EMEA/H/C/3", "Hyperdrug", "Authorised", "09/01/2021", "Hypertension"),
    ]
    build_approvals(us, eu)

    search = {"condition": "Diabetes Mellitus, Type 2", "terms": ["type 2 diabetes"]}
    response = TestClient(app).post("/approvals", json=search)

    assert response.status_code == 200
    assert response.json()["approvals"][1]["research_medicine"] == "metformin"
    found = [
        (a["region"], a["approval_id"], a["medicine_name"], a["status"], a["approved_on"])
        for a in response.json()["approvals"]
    ]
    assert found == [
        ("US", "BLA000002", "NEWBRAND", "Marketed", "2024-06-01"),
        ("EU", "EMEA/H/C/1", "Eudrug", "Authorised", "2017-01-09"),
        ("US", "NDA000001", "OLDBRAND", "Marketed", "1999-01-01"),
    ]

    model = FakeModel(
        Intake(
            kind="approval_search",
            condition="Diabetes Mellitus, Type 2",
            terms=["type 2 diabetes", "T2D"],
        )
    )
    monkeypatch.setattr(agent, "chat_model", lambda **options: model)
    response = TestClient(app).post("/approvals", json={"condition": "T2D", "expand_terms": True})
    assert response.status_code == 200
    body = response.json()
    assert body["search"]["condition"] == "Diabetes Mellitus, Type 2"
    assert body["search"]["terms"] == ["T2D", "type 2 diabetes"]
    assert [approval["approval_id"] for approval in body["approvals"]] == [row[1] for row in found]


def test_approvals_still_use_the_entered_condition_when_the_agent_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unavailable(**options: Any) -> None:
        raise TimeoutError

    def lookup(search: approvals.ApprovalSearch) -> approvals.ApprovalResults:
        assert search.condition == "Psoriasis"
        assert search.terms == []
        return approvals.ApprovalResults(approvals=[])

    monkeypatch.setattr(agent, "chat_model", unavailable)
    monkeypatch.setattr(approvals, "find_approvals", lookup)
    response = TestClient(app).post(
        "/approvals", json={"condition": "Psoriasis", "expand_terms": True}
    )
    assert response.status_code == 200
    assert response.json()["search"]["condition"] == "Psoriasis"
    assert response.json()["search"]["terms"] == []


def test_hair_loss_searches_match_fda_label_wording_without_a_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build_approvals(
        [
            us_approval(
                "NDA020788",
                "PROPECIA",
                "19971219",
                "For male pattern hair loss (androgenetic alopecia).",
            ),
            us_approval(
                "NDA020834", "ROGAINE", "19971114", "Use to regrow hair on the top of the scalp."
            ),
            us_approval("NDA018154", "LONITEN", "19790812", "For hypertension."),
        ],
        [eu_approval("EMEA/H/C/1", "AreataDrug", "Authorised", "09/01/2020", "Alopecia Areata")],
    )

    def unavailable(**options: Any) -> None:
        raise TimeoutError

    monkeypatch.setattr(agent, "chat_model", unavailable)
    client = TestClient(app)
    for condition in ("androgenic alopecia", " Male  Hair Loss ", "hair loss"):
        response = client.post("/approvals", json={"condition": condition, "expand_terms": True})
        assert response.status_code == 200
        body = response.json()
        expected = ["NDA020788", "NDA020834"]
        if condition == "hair loss":
            expected.insert(0, "EMEA/H/C/1")
        assert [approval["approval_id"] for approval in body["approvals"]] == expected
        assert "regrow hair" in body["search"]["terms"]


def test_rejects_more_than_three_terms() -> None:
    search = {"condition": "Psoriasis", "terms": ["a1", "b2", "c3", "d4"]}
    response = TestClient(app).post("/approvals", json=search)
    assert response.status_code == 422


def test_says_when_the_approval_tables_are_not_built() -> None:
    response = TestClient(app).post("/approvals", json={"condition": "Psoriasis"})
    assert response.status_code == 503


def test_says_when_only_the_trial_tables_are_built() -> None:
    save_load("2026-11-16", [FULL_STUDY])
    transform.build()
    response = TestClient(app).post("/approvals", json={"condition": "Psoriasis"})
    assert response.status_code == 503
