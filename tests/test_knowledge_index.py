import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from agent_product.core.config import Settings
from agent_product.services.knowledge_index import (
    EmbeddingModel,
    InMemoryLexicalKnowledgeIndex,
    KnowledgeIndexChunk,
    KnowledgeIndexQuery,
    SQLiteFtsKnowledgeIndex,
)


def make_chunk(
    chunk_id: str,
    content: str,
    *,
    document_id: str = "guide",
    tags: tuple[str, ...] = ("product",),
    content_hash: str | None = None,
) -> KnowledgeIndexChunk:
    return KnowledgeIndexChunk(
        chunk_id=chunk_id,
        parent_chunk_id=f"parent-{chunk_id}",
        document_id=document_id,
        title="产品指南",
        summary="产品知识",
        source=f"{document_id}.md",
        source_uri=f"kb://{document_id}",
        heading_path=("产品指南", "到账时间"),
        tags=tags,
        content=content,
        context=content,
        line_start=10,
        line_end=12,
        context_line_start=10,
        context_line_end=12,
        token_count=20,
        policy_version=1,
        updated_at="2026-08-16T00:00:00+00:00",
        revision="revision",
        content_hash=content_hash or f"hash-{chunk_id}",
    )


def test_local_index_backend_defaults_by_environment() -> None:
    development = Settings(
        app_env="development",
        knowledge_index_backend="auto",
        _env_file=None,
    )
    test = Settings(app_env="test", knowledge_index_backend="auto", _env_file=None)

    assert development.knowledge_index_backend == "sqlite_fts5"
    assert test.knowledge_index_backend == "memory"


def test_lexical_index_sync_is_incremental_and_reports_status() -> None:
    index = InMemoryLexicalKnowledgeIndex()
    first = make_chunk("one", "退款会在三个工作日内到账")
    second = make_chunk("two", "部署前需要运行测试", document_id="deploy")

    initial = index.sync([first, second])
    unchanged = index.sync([first, second])
    changed = index.sync([replace(first, content_hash="new-hash")])

    assert (initial.added, initial.updated, initial.removed, initial.total) == (2, 0, 0, 2)
    assert (unchanged.added, unchanged.updated, unchanged.removed) == (0, 0, 0)
    assert (changed.added, changed.updated, changed.removed, changed.total) == (0, 1, 1, 1)
    assert index.status().backend == "memory_lexical"
    assert index.status().retrieval_modes == ("lexical",)
    assert index.status().chunk_count == 1
    assert index.status().embedding_model is None
    assert index.get("one") is not None
    assert index.get("one").content_hash == "new-hash"
    assert index.get("missing") is None


def test_lexical_index_search_uses_standard_query_and_score_contract() -> None:
    index = InMemoryLexicalKnowledgeIndex()
    refund = make_chunk("refund", "退款会在三个工作日内原路到账")
    deploy = make_chunk(
        "deploy",
        "部署前需要运行测试",
        document_id="deploy",
        tags=("engineering",),
    )
    index.sync([refund, deploy])

    matches = index.search(KnowledgeIndexQuery(text="退款多久到账", limit=5))
    filtered = index.search(
        KnowledgeIndexQuery(
            text="退款",
            required_tags=frozenset({"engineering"}),
        )
    )

    assert matches[0].chunk.chunk_id == "refund"
    assert matches[0].score == matches[0].lexical_score
    assert matches[0].vector_score is None
    assert filtered == []


def test_open_source_embedding_adapters_only_need_to_implement_the_protocol() -> None:
    class FakeEmbeddingModel:
        model_id = "local:test-embedding"
        dimensions = 3

        def embed_documents(self, texts):
            return [[float(len(text)), 0.0, 1.0] for text in texts]

        def embed_query(self, text):
            return [float(len(text)), 0.0, 1.0]

    model = FakeEmbeddingModel()

    assert isinstance(model, EmbeddingModel)
    assert model.embed_documents(["知识"])[0] == [2.0, 0.0, 1.0]
    assert model.embed_query("检索") == [2.0, 0.0, 1.0]


def test_sqlite_fts_index_persists_and_incrementally_syncs(tmp_path: Path) -> None:
    path = tmp_path / "knowledge-index.db"
    refund = make_chunk("refund", "退款会在三个工作日内原路到账")
    deploy = make_chunk(
        "deploy",
        "部署前需要运行测试",
        document_id="deploy",
        tags=("engineering",),
    )
    index = SQLiteFtsKnowledgeIndex(path)

    initial = index.sync([refund, deploy])
    matches = index.search(KnowledgeIndexQuery(text="退款多久到账", limit=5))
    index.close()

    reopened = SQLiteFtsKnowledgeIndex(path)
    persisted = reopened.search(KnowledgeIndexQuery(text="退款多久到账", limit=5))
    filtered = reopened.search(
        KnowledgeIndexQuery(text="退款", required_tags=frozenset({"engineering"}))
    )
    changed = reopened.sync([replace(refund, content_hash="new-hash")])

    assert (initial.added, initial.updated, initial.removed, initial.total) == (2, 0, 0, 2)
    assert matches[0].chunk.chunk_id == "refund"
    assert persisted[0].chunk.chunk_id == "refund"
    assert reopened.get("refund") is not None
    assert reopened.get("missing") is None
    assert filtered == []
    assert (changed.added, changed.updated, changed.removed, changed.total) == (0, 1, 1, 1)
    assert reopened.status().backend == "sqlite_fts5"
    assert reopened.status().retrieval_modes == ("lexical", "persistent")
    reopened.close()


def test_sqlite_fts_index_refuses_unknown_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "future-index.db"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE knowledge_index_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        "INSERT INTO knowledge_index_meta(key, value) VALUES ('schema_version', '999')"
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeError, match="Unsupported knowledge index schema"):
        SQLiteFtsKnowledgeIndex(path)
