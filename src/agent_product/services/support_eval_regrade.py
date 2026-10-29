"""Reapply deterministic checks to saved traces without generating model responses."""

from __future__ import annotations

import gzip
import hashlib
import json
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_product.services.support_eval import (
    PROJECT_ROOT,
    _tree_digest,
    summarize_support_results,
    write_support_report,
)
from agent_product.services.support_eval_data import EvalData
from agent_product.services.support_eval_grading import RUBRIC_VERSION, grade_support_run
from agent_product.services.support_eval_tasks import load_support_eval_tasks

SCENARIO_FIELDS = ("id", "number", "group", "customer", "turns", "approvals", "poison_knowledge")


def regrade_support_report(
    source: Path,
    *,
    output: Path,
    tasks_path: Path | None = None,
) -> dict[str, Any]:
    """Preserve original replies/usage and reject changed scenarios or destructive writes.

    Only expectations may differ. A report generated under another conversation,
    customer, approval policy or injection setup cannot validate a new scenario.
    Recorded tokens and elapsed time belong to the source run, not this regrade.
    """
    if source.resolve() == output.resolve() or output.exists():
        raise FileExistsError("Choose a new output path; original reports are never overwritten")
    source_bytes = source.read_bytes()
    if source.suffix == ".gz":
        # Large raw reports are stored compressed; the hash still describes the JSON bytes.
        source_bytes = gzip.decompress(source_bytes)
    original = json.loads(source_bytes)
    if not original.get("task_definitions"):
        raise ValueError("The source report must contain its original task definitions")
    original_tasks = {task["number"]: task for task in original["task_definitions"]}
    candidates = (
        load_support_eval_tasks(tasks_path)
        if tasks_path is not None
        else tuple(original["task_definitions"])
    )
    tasks = {task["number"]: task for task in candidates}
    selected: dict[int, dict[str, Any]] = {}
    for number in original["task_numbers"]:
        if number not in tasks or number not in original_tasks:
            raise ValueError(f"Missing task definition for {number}")
        before, after = original_tasks[number], tasks[number]
        if any(before.get(field) != after.get(field) for field in SCENARIO_FIELDS):
            raise ValueError(f"Task {number} changed its scenario; a new model run is required")
        selected[number] = after

    report = deepcopy(original)
    changes = []
    seen: set[tuple[str, int, int]] = set()
    for row in report["results"]:
        number = row["number"]
        if number not in selected:
            raise ValueError(f"Result contains an unselected task: {number}")
        identity = (row["model"], number, row["repeat"])
        if identity in seen:
            raise ValueError(f"Duplicate result identity: {identity}")
        seen.add(identity)
        row["previous_passed"] = row["passed"]
        row["previous_failures"] = deepcopy(row["failures"])
        if "data" in row:
            row["failures"] = grade_support_run(selected[number], row, EvalData(**row["data"]))
        elif row.get("error"):
            row["failures"] = [{"rule": "runtime_error", "message": row["error"], "turn": None}]
        else:
            raise ValueError(f"Task {number} has no recorded fixture data")
        row["passed"] = not row["failures"]
        if row["passed"] != row["previous_passed"] or row["failures"] != row["previous_failures"]:
            changes.append(
                {
                    "model": row["model"],
                    "task": number,
                    "repeat": row["repeat"],
                    "previous_passed": row["previous_passed"],
                    "passed": row["passed"],
                    "previous_rules": [f["rule"] for f in row["previous_failures"]],
                    "rules": [f["rule"] for f in row["failures"]],
                }
            )
    report["task_definitions"] = list(selected.values())
    report["previous_summary"] = deepcopy(original["summary"])
    report["summary"] = summarize_support_results(report["results"], repeat=report["repeat"])
    report["regrade"] = {
        "source_report": str(source.resolve()),
        "source_sha256": hashlib.sha256(source_bytes).hexdigest(),
        "regraded_at": datetime.now(UTC).isoformat(),
        "rubric_version": RUBRIC_VERSION,
        "grader_sha256": _tree_digest(
            PROJECT_ROOT / "src/agent_product/services/support_eval_grading.py"
        ),
        "tasks_sha256": _tree_digest(tasks_path) if tasks_path is not None else None,
        "changes": changes,
        "new_api_requests": 0,
        "new_token_usage": 0,
        "usage_origin": "Original model execution; no tokens were generated during regrading.",
    }
    report["metadata"]["original_execution_mode"] = original["metadata"]["execution_mode"]
    report["metadata"]["execution_mode"] = "offline_regrade"
    # Keep original source hashes under metadata: they describe response generation.
    # The new grader and task hashes are separately recorded under regrade.
    write_support_report(output, report)
    return report
