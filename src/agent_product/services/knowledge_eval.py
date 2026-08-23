from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from agent_product.services.knowledge_provider import KnowledgeProvider


@dataclass(frozen=True, slots=True)
class KnowledgeEvalCase:
    id: str
    query: str
    expected_document_ids: tuple[str, ...]
    tags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class KnowledgeEvalCaseResult:
    id: str
    query: str
    expected_document_ids: tuple[str, ...]
    returned_document_ids: tuple[str, ...]
    first_relevant_rank: int | None

    @property
    def hit(self) -> bool:
        return self.first_relevant_rank is not None

    @property
    def reciprocal_rank(self) -> float:
        return 0.0 if self.first_relevant_rank is None else 1 / self.first_relevant_rank


@dataclass(frozen=True, slots=True)
class KnowledgeEvalReport:
    case_count: int
    hit_rate: float
    mean_reciprocal_rank: float
    results: tuple[KnowledgeEvalCaseResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "hit_rate": self.hit_rate,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "results": [
                {
                    **asdict(result),
                    "hit": result.hit,
                    "reciprocal_rank": result.reciprocal_rank,
                }
                for result in self.results
            ],
        }


def load_knowledge_eval_cases(path: str | Path) -> tuple[KnowledgeEvalCase, ...]:
    cases: list[KnowledgeEvalCase] = []
    for line_number, raw_line in enumerate(
        Path(path).read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        if not raw_line.strip():
            continue
        try:
            payload = json.loads(raw_line)
            case = KnowledgeEvalCase(
                id=str(payload["id"]),
                query=str(payload["query"]),
                expected_document_ids=tuple(payload["expected_document_ids"]),
                tags=tuple(payload.get("tags", ())),
            )
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError(f"Invalid knowledge eval case at line {line_number}") from exc
        if not case.id or not case.query or not case.expected_document_ids:
            raise ValueError(f"Invalid knowledge eval case at line {line_number}")
        cases.append(case)
    if not cases:
        raise ValueError("Knowledge evaluation dataset is empty")
    return tuple(cases)


def evaluate_knowledge(
    provider: KnowledgeProvider,
    cases: tuple[KnowledgeEvalCase, ...],
    *,
    limit: int = 5,
) -> KnowledgeEvalReport:
    results: list[KnowledgeEvalCaseResult] = []
    for case in cases:
        response = provider.search(
            case.query,
            limit=limit,
            tags=list(case.tags) or None,
        )
        returned = tuple(
            dict.fromkeys(
                str(result["document_id"]) for result in response.get("results", ())
            )
        )
        expected = set(case.expected_document_ids)
        first_rank = next(
            (rank for rank, document_id in enumerate(returned, start=1) if document_id in expected),
            None,
        )
        results.append(
            KnowledgeEvalCaseResult(
                id=case.id,
                query=case.query,
                expected_document_ids=case.expected_document_ids,
                returned_document_ids=returned,
                first_relevant_rank=first_rank,
            )
        )
    count = len(results)
    return KnowledgeEvalReport(
        case_count=count,
        hit_rate=round(sum(result.hit for result in results) / count, 4),
        mean_reciprocal_rank=round(
            sum(result.reciprocal_rank for result in results) / count,
            4,
        ),
        results=tuple(results),
    )
