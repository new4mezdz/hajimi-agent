"""Run isolated support evaluations; defaults to the five-case pilot."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from dotenv import load_dotenv
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.services.support_eval import (
    DEFAULT_TASKS,
    PROJECT_ROOT,
    EvalLimits,
    evaluate_support,
)
from agent_product.services.support_eval_tasks import load_support_eval_tasks


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tasks", type=Path, default=DEFAULT_TASKS)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--only", help="Comma-separated task numbers; default: 1,6,16,21,29")
    selection.add_argument("--all", action="store_true", help="Explicitly select the full dataset")
    parser.add_argument("--repeat", type=int, default=1)
    parser.add_argument("--model", action="append", help="Repeat this option to compare models")
    parser.add_argument("--output", type=Path, default=Path("evals/support/reports/pilot.json"))
    parser.add_argument(
        "--validate-only", action="store_true", help="Validate tasks without API calls"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Use an inert fake model; no API calls"
    )
    parser.add_argument("--max-requests", type=int, default=20)
    parser.add_argument("--max-total-tokens", type=int, default=60_000)
    parser.add_argument("--max-output-tokens", type=int, default=4_096)
    parser.add_argument("--max-approval-rounds", type=int, default=5)
    parser.add_argument("--timeout", type=float, default=180)
    args = parser.parse_args()
    try:
        tasks = load_support_eval_tasks(args.tasks)
        if args.validate_only:
            print(json.dumps({"valid": True, "task_count": len(tasks)}, ensure_ascii=False))
            return
        if args.repeat < 1:
            raise ValueError("--repeat must be positive")
        only = (
            None if args.all else [int(n.strip()) for n in (args.only or "1,6,16,21,29").split(",")]
        )
        if only is not None and set(only) - {task["number"] for task in tasks}:
            raise ValueError("--only contains unknown task numbers")
        limits = EvalLimits(
            max_requests=args.max_requests,
            max_total_tokens=args.max_total_tokens,
            max_output_tokens=args.max_output_tokens,
            max_approval_rounds=args.max_approval_rounds,
            timeout_seconds=args.timeout,
        )
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    # Explicit path also works when invoked from outside the repository or from stdin.
    load_dotenv(PROJECT_ROOT / ".env", override=False)
    settings = Settings(_env_file=PROJECT_ROOT / ".env")
    if args.dry_run:
        settings = settings.model_copy(update={"ai_model": "test"})
        if args.model:
            parser.error("--dry-run cannot be combined with --model")
    if args.output.exists():
        parser.error(f"Report already exists; choose a new --output path: {args.output}")

    def progress(result: dict) -> None:
        print(
            json.dumps(
                {
                    "task": result["number"],
                    "repeat": result["repeat"],
                    "passed": result["passed"],
                    "failures": [failure["rule"] for failure in result["failures"]],
                    "usage": result["usage"],
                    "elapsed_seconds": result["elapsed_seconds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    report = asyncio.run(
        evaluate_support(
            settings,
            tasks_path=args.tasks,
            only=only,
            repeat=args.repeat,
            models=args.model,
            output=args.output,
            limits=limits,
            progress=progress,
            model_factory=(
                lambda: TestModel(call_tools=[], custom_output_text="这是评测运行器的离线检查。")
            )
            if args.dry_run
            else None,
        )
    )
    print(
        json.dumps(
            {"report": str(args.output.resolve()), "summary": report["summary"]},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
