import json
from pathlib import Path

import pytest

from agent_product.services.knowledge_eval import (
    KnowledgeEvalCase,
    evaluate_knowledge,
    load_knowledge_eval_cases,
)


class FakeKnowledgeProvider:
    def search(self, query, *, limit=5, tags=None):
        del limit, tags
        results = {
            "refund": ["other", "refund-policy"],
            "deploy": ["deployment-guide"],
        }.get(query, [])
        return {"results": [{"document_id": document_id} for document_id in results]}

    def read_document(self, document_id, *, start_line=1, end_line=240):
        raise NotImplementedError

    def get_document(self, document_id, *, include_inactive=False):
        raise NotImplementedError


def test_knowledge_evaluation_reports_hit_rate_and_mrr() -> None:
    report = evaluate_knowledge(
        FakeKnowledgeProvider(),
        (
            KnowledgeEvalCase("refund", "refund", ("refund-policy",)),
            KnowledgeEvalCase("deploy", "deploy", ("deployment-guide",)),
            KnowledgeEvalCase("missing", "missing", ("missing-guide",)),
        ),
    )

    assert report.case_count == 3
    assert report.hit_rate == 0.6667
    assert report.mean_reciprocal_rank == 0.5
    assert report.results[0].first_relevant_rank == 2
    assert report.results[2].first_relevant_rank is None


def test_knowledge_evaluation_jsonl_loader_validates_cases(tmp_path: Path) -> None:
    dataset = tmp_path / "knowledge.jsonl"
    dataset.write_text(
        json.dumps(
            {
                "id": "refund",
                "query": "退款多久到账",
                "expected_document_ids": ["refund-policy"],
                "tags": ["policy"],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    cases = load_knowledge_eval_cases(dataset)

    assert cases[0].id == "refund"
    assert cases[0].tags == ("policy",)

    dataset.write_text("{}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="line 1"):
        load_knowledge_eval_cases(dataset)
