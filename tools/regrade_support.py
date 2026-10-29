from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_product.services.support_eval_regrade import regrade_support_report


def main() -> None:
    parser = argparse.ArgumentParser(description="Regrade saved support traces without API calls")
    parser.add_argument("report", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument(
        "--tasks", type=Path, help="Optional revised expectations; scenarios must match"
    )
    args = parser.parse_args()
    try:
        report = regrade_support_report(args.report, output=args.output, tasks_path=args.tasks)
    except (OSError, ValueError) as exc:
        parser.error(str(exc))
    print(
        json.dumps(
            {
                "output": str(args.output.resolve()),
                "new_api_requests": 0,
                "changed_results": len(report["regrade"]["changes"]),
                "summary": report["summary"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
