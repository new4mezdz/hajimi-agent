from __future__ import annotations

import argparse
import json

from agent_product.core.config import Settings
from agent_product.services.knowledge import KnowledgeBase
from agent_product.services.knowledge_eval import (
    evaluate_knowledge,
    load_knowledge_eval_cases,
)
from agent_product.services.knowledge_index import create_knowledge_index


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate local knowledge retrieval")
    parser.add_argument("dataset", help="JSONL file containing retrieval expectations")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args()
    if args.limit < 1:
        parser.error("--limit must be positive")

    settings = Settings()
    index = create_knowledge_index(
        settings.knowledge_index_backend,
        sqlite_path=settings.knowledge_index_path,
    )
    try:
        knowledge = KnowledgeBase(settings.knowledge_dir, index=index)
        report = evaluate_knowledge(
            knowledge,
            load_knowledge_eval_cases(args.dataset),
            limit=args.limit,
        )
        print(json.dumps(report.as_dict(), ensure_ascii=False, indent=2))
    finally:
        close = getattr(index, "close", None)
        if close is not None:
            close()


if __name__ == "__main__":
    main()
