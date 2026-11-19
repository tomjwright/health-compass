"""Evals: ask the agent fixed questions about fixed trials, and fail when a score drops.

    uv run --env-file .env python -m health_compass.evals

The trials are 30 real ClinicalTrials.gov records in evals/trials.json.gz, and the approvals
are 30 real openFDA and EMA records in evals/approvals.json.gz, so every run searches the same
data. The cases and the thresholds are in evals/cases.yaml. CI runs this as
the release gate: a score below its threshold fails the build, so the change can't merge.
"""

import gzip
import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

import dlt
import yaml
from langchain_core.language_models import BaseChatModel

from health_compass import transform
from health_compass.agent import Answer, TrialAgent, chat_model, judge_model
from health_compass.approval_agent import ApprovalAgent, ApprovalAnswer
from health_compass.ingest import common
from health_compass.ingest.clinicaltrials import load_studies
from health_compass.judge import unsupported_approval_notes, unsupported_notes
from health_compass.telemetry import setup_logging

log = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).parents[2] / "evals"
RESULTS_FILE = Path("data/eval_results.json")


def build_eval_tables(workdir: Path) -> None:
    """Builds the trial and approval tables in `workdir` from the trials and approvals files."""
    os.environ["RAW_DUCKDB_PATH"] = str(workdir / "raw.duckdb")
    os.environ["DLT_DATA_DIR"] = str(workdir / "dlt")
    os.environ["DUCKDB_PATH"] = str(workdir / "health_compass.duckdb")
    page = json.loads(gzip.decompress((EVALS_DIR / "trials.json.gz").read_bytes()))
    load_studies(page["studies"], "2026-11-17T00:00:00")
    approvals = json.loads(gzip.decompress((EVALS_DIR / "approvals.json.gz").read_bytes()))
    common.pipeline("openfda").run(
        [
            dlt.resource(approvals["applications"], name="applications"),
            dlt.resource(approvals["labels"], name="labels"),
        ]
    )
    common.pipeline("ema").run(dlt.resource(approvals["medicines"], name="medicines"))
    transform.build()


def run_evals(
    model: BaseChatModel, judge: BaseChatModel, suite: dict[str, Any]
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """The scores, and each case's result, from asking the agents every question in the suite:
    `cases` go to the trial agent and `approval_cases` to the approvals agent."""
    trial_agent = TrialAgent(model, judge)
    approval_agent = ApprovalAgent(model, judge)
    cases = []
    for case in suite["cases"]:
        cases.append(("trials", case))
    for case in suite.get("approval_cases", []):
        cases.append(("approvals", case))
    results = []
    correct = 0
    should_refuse = 0
    refused = 0
    notes = 0
    unsupported_count = 0
    for agent, case in cases:
        answer: Answer | ApprovalAnswer
        if agent == "trials":
            answer = trial_agent.ask(case["question"], suite["latitude"], suite["longitude"])
        else:
            answer = approval_agent.ask(case["question"])
        passed = answer.outcome == case["expected"]
        if passed:
            correct += 1
        if case["expected"] == "refused":
            should_refuse += 1
            if answer.outcome == "refused":
                refused += 1
        unsupported = []
        # The agent's own gate already ran the judge; checking again here makes the score
        # drop if that gate is ever removed or weakened.
        if answer.outcome == "answered" and isinstance(answer, Answer):
            trial_notes = {}
            for trial in answer.trials:
                if trial.note is not None:
                    trial_notes[trial.nct_id] = trial.note
            notes += len(trial_notes)
            unsupported = unsupported_notes(judge, answer.trials, trial_notes)
        elif answer.outcome == "answered" and isinstance(answer, ApprovalAnswer):
            approval_notes = {}
            for approval in answer.approvals:
                if approval.note is not None:
                    approval_notes[approval.approval_id] = approval.note
            notes += len(approval_notes)
            unsupported = unsupported_approval_notes(judge, answer.approvals, approval_notes)
        unsupported_count += len(unsupported)
        result = {
            "agent": agent,
            "id": case["id"],
            "expected": case["expected"],
            "outcome": answer.outcome,
            "unsupported_notes": unsupported,
        }
        print(("pass" if passed else "FAIL") + f"  {json.dumps(result)}")
        results.append(result)
    scores = {
        "outcome_accuracy": correct / len(cases),
        "refusal_recall": refused / should_refuse if should_refuse else 1.0,
        "groundedness": (notes - unsupported_count) / notes if notes else 1.0,
    }
    return scores, results


def failed_thresholds(scores: dict[str, float], thresholds: dict[str, float]) -> list[str]:
    failed = []
    for name, minimum in thresholds.items():
        if scores[name] < minimum:
            failed.append(f"{name} {scores[name]:.2f} is below {minimum}")
    return failed


def main() -> None:
    setup_logging()
    suite = yaml.safe_load((EVALS_DIR / "cases.yaml").read_text())
    with tempfile.TemporaryDirectory() as workdir:
        build_eval_tables(Path(workdir))
        scores, results = run_evals(chat_model(), judge_model(), suite)
    RESULTS_FILE.parent.mkdir(exist_ok=True)
    RESULTS_FILE.write_text(json.dumps({"scores": scores, "cases": results}, indent=2))
    for name, score in scores.items():
        print(f"{name}: {score:.2f} (needs {suite['thresholds'][name]})")
    failed = failed_thresholds(scores, suite["thresholds"])
    if failed:
        sys.exit("Release gate failed: " + "; ".join(failed))
    print("Release gate passed.")


if __name__ == "__main__":
    main()
