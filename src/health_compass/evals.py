"""Evals: ask the agent fixed questions about fixed trials, and fail when a score drops.

    uv run --env-file .env python -m health_compass.evals

The trials are 30 real ClinicalTrials.gov records in evals/trials.json.gz, so every run
searches the same data. The cases and the thresholds are in evals/cases.yaml. CI runs this as
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

import yaml
from langchain_core.language_models import BaseChatModel

from health_compass import transform
from health_compass.agent import Supervisor, chat_model, judge_model
from health_compass.ingest.clinicaltrials import load_studies
from health_compass.telemetry import setup_logging

log = logging.getLogger(__name__)

EVALS_DIR = Path(__file__).parents[2] / "evals"
RESULTS_FILE = Path("data/eval_results.json")


def build_eval_tables(workdir: Path) -> None:
    """Builds the trial tables in `workdir` from the trials file, as one load."""
    os.environ["RAW_DUCKDB_PATH"] = str(workdir / "raw.duckdb")
    os.environ["DLT_DATA_DIR"] = str(workdir / "dlt")
    os.environ["DUCKDB_PATH"] = str(workdir / "health_compass.duckdb")
    page = json.loads(gzip.decompress((EVALS_DIR / "trials.json.gz").read_bytes()))
    load_studies(page["studies"], "2026-11-17T00:00:00")
    transform.build()


def run_evals(
    model: BaseChatModel, judge: BaseChatModel, suite: dict[str, Any]
) -> tuple[dict[str, float], list[dict[str, Any]]]:
    """The scores, and each case's result, from asking the agent every question in the suite."""
    supervisor = Supervisor(model, judge)
    results = []
    correct = 0
    should_refuse = 0
    refused = 0
    notes = 0
    unsupported_count = 0
    for case in suite["cases"]:
        answer = supervisor.ask(case["question"], suite["latitude"], suite["longitude"])
        passed = answer.outcome == case["expected"]
        if passed:
            correct += 1
        if case["expected"] == "refused":
            should_refuse += 1
            if answer.outcome == "refused":
                refused += 1
        unsupported = []
        if answer.outcome == "answered":
            # The agent's own gate already ran the judge; checking again here makes the score
            # drop if that gate is ever removed or weakened.
            answer_notes = {}
            for number, trial in enumerate(answer.trials, 1):
                if trial.note is not None:
                    answer_notes[number] = trial.note
            notes += len(answer_notes)
            trials = supervisor.agents["trials"]
            numbers = trials.judge_notes(answer.trials, answer_notes)
            unsupported = [trials.id_of(answer.trials[number - 1]) for number in numbers]
            unsupported_count += len(unsupported)
        result = {
            "id": case["id"],
            "expected": case["expected"],
            "outcome": answer.outcome,
            "unsupported_notes": unsupported,
        }
        print(("pass" if passed else "FAIL") + f"  {json.dumps(result)}")
        results.append(result)
    scores = {
        "outcome_accuracy": correct / len(suite["cases"]),
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
