from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.main import create_app
from agent_product.services.agent import AgentDependencies, build_agent
from agent_product.services.knowledge import (
    CHUNK_FORCED_OVERLAP_TOKENS,
    CHUNK_HARD_MAX_TOKENS,
    CHUNK_POLICY_VERSION,
    PARENT_CONTEXT_MAX_TOKENS,
    KnowledgeBase,
    _estimated_token_count,
)


def write_document(root: Path, relative_path: str, content: str) -> None:
    path = root / relative_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def make_knowledge_base(tmp_path: Path) -> KnowledgeBase:
    root = tmp_path / "knowledge"
    write_document(
        root,
        "product/refunds.md",
        """---
id: product/refunds
title: 退款处理政策
summary: 客服处理订单退款时使用的正式规则
tags: [product, policy, refund]
status: active
---

# 退款处理政策

## 到账时间

审核通过后，退款通常在三个工作日内原路到账。

## 例外

银行维护期间可能延迟，客服需要引用支付平台流水号。
""",
    )
    write_document(
        root,
        "drafts/future.md",
        """---
id: drafts/future
title: 未发布退款规则
tags: [refund]
status: draft
---

# 未发布退款规则

退款立即到账。
""",
    )
    return KnowledgeBase(root)


def test_search_returns_ranked_excerpt_with_source_lines(tmp_path: Path) -> None:
    knowledge_base = make_knowledge_base(tmp_path)

    result = knowledge_base.search("退款多久到账", limit=3)

    assert result["count"] >= 1
    first = result["results"][0]
    assert first["document_id"] == "product/refunds"
    assert first["section"] == "到账时间"
    assert "三个工作日" in first["snippet"]
    assert first["source"] == "product/refunds.md"
    assert first["line_start"] > 1
    assert "product/refunds.md:L" in first["citation"]
    assert first["context"] is None
    context = knowledge_base.read_context(first["chunk_id"])
    assert context["document_id"] == "product/refunds"
    assert "三个工作日" in context["context"]
    assert context["citation"]


def test_document_status_and_tag_filters_are_enforced(tmp_path: Path) -> None:
    knowledge_base = make_knowledge_base(tmp_path)

    documents = knowledge_base.list_documents()
    matching = knowledge_base.search("退款", tags=["policy"])
    missing = knowledge_base.search("退款", tags=["architecture"])

    assert [document["document_id"] for document in documents] == ["product/refunds"]
    assert matching["count"] >= 1
    assert missing["results"] == []


def test_repeated_search_reuses_unchanged_document_and_chunk_snapshots(
    tmp_path: Path,
    monkeypatch,
) -> None:
    knowledge_base = make_knowledge_base(tmp_path)
    original_chunks = knowledge_base._chunks
    calls = 0

    def counted_chunks(document):
        nonlocal calls
        calls += 1
        return original_chunks(document)

    monkeypatch.setattr(knowledge_base, "_chunks", counted_chunks)

    knowledge_base.search("退款")
    knowledge_base.search("多久到账")

    assert calls == 1

    source = knowledge_base.root / "product" / "refunds.md"
    source.write_text(
        source.read_text(encoding="utf-8").replace("三个工作日", "四个工作日"),
        encoding="utf-8",
    )
    refreshed = knowledge_base.search("四个工作日")

    assert calls == 2
    assert "四个工作日" in refreshed["results"][0]["snippet"]


def test_chunk_policy_preserves_heading_boundaries_and_short_documents(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    write_document(
        root,
        "guides/structured.md",
        """---
id: guides/structured
title: 结构化指南
status: active
---

# 结构化指南

这是一段很短的文档介绍，不需要进行固定长度切分。

## 安装

安装章节只包含安装相关的信息。

## 运维

运维章节只包含运维相关的信息。
""",
    )
    knowledge_base = KnowledgeBase(root)
    document = knowledge_base._documents()[0]

    chunks = knowledge_base._chunks(document)

    assert [chunk.heading_path for chunk in chunks] == [
        ("结构化指南",),
        ("结构化指南", "安装"),
        ("结构化指南", "运维"),
    ]
    assert all(chunk.policy_version == CHUNK_POLICY_VERSION for chunk in chunks)
    assert all(chunk.context == chunk.text for chunk in chunks)
    assert "安装章节" not in chunks[0].text
    assert "运维章节" not in chunks[1].text


def test_chunk_policy_forced_split_has_hard_limit_overlap_and_parent_context(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    long_paragraph = "甲" * 1_700
    write_document(
        root,
        "guides/long.md",
        """---
id: guides/long
title: 超长指南
status: active
---

# 超长指南

"""
        + long_paragraph,
    )
    knowledge_base = KnowledgeBase(root)
    document = knowledge_base._documents()[0]

    chunks = knowledge_base._chunks(document)

    assert len(chunks) > 1
    assert all(chunk.token_count <= CHUNK_HARD_MAX_TOKENS for chunk in chunks)
    assert (
        chunks[0].text[-CHUNK_FORCED_OVERLAP_TOKENS:]
        == chunks[1].text[:CHUNK_FORCED_OVERLAP_TOKENS]
    )
    assert all(chunk.chunk_id.startswith("kbch_") for chunk in chunks)
    assert all(chunk.parent_chunk_id.startswith("kbctx_") for chunk in chunks)
    assert all(
        _estimated_token_count(chunk.context) <= PARENT_CONTEXT_MAX_TOKENS for chunk in chunks
    )


def test_chunk_policy_keeps_tables_and_code_blocks_as_standalone_elements(
    tmp_path: Path,
) -> None:
    root = tmp_path / "knowledge"
    write_document(
        root,
        "guides/elements.md",
        """---
id: guides/elements
title: 元素指南
status: active
---

# 元素指南

表格前的解释。

| 名称 | 状态 |
| --- | --- |
| Agent | 可用 |

```python
def answer():
    return 42
```

代码后的解释。
""",
    )
    knowledge_base = KnowledgeBase(root)
    document = knowledge_base._documents()[0]

    chunks = knowledge_base._chunks(document)

    table = next(chunk for chunk in chunks if chunk.text.startswith("| 名称"))
    code = next(chunk for chunk in chunks if chunk.text.startswith("```python"))
    assert table.text.endswith("| Agent | 可用 |")
    assert code.text.endswith("```")
    assert not table.mergeable
    assert not code.mergeable


def test_read_document_uses_stable_id_and_numbered_lines(tmp_path: Path) -> None:
    knowledge_base = make_knowledge_base(tmp_path)

    document = knowledge_base.read_document("product/refunds", start_line=9, end_line=12)

    assert document["document_id"] == "product/refunds"
    assert document["source_uri"] == "kb://product/refunds"
    assert "9:" in document["content"]
    assert document["end_line"] == 12


def test_create_update_publish_and_archive_document(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    knowledge_base = KnowledgeBase(root)

    created = knowledge_base.save_document(
        document_id="product/new-guide",
        title="新产品指南",
        summary="新产品的内部说明",
        tags=["Product", "Guide"],
        status="draft",
        body="# 新产品指南\n\n这是草稿。",
    )

    assert created["action"] == "created"
    assert created["status"] == "draft"
    assert knowledge_base.search("这是草稿")["count"] == 0

    published = knowledge_base.save_document(
        document_id="product/new-guide",
        title="新产品指南",
        summary="新产品的内部说明",
        tags=["product", "guide"],
        status="active",
        body="# 新产品指南\n\n这是已经发布的内容。",
        expected_revision=created["revision"],
    )

    assert published["action"] == "updated"
    assert published["revision"] != created["revision"]
    assert knowledge_base.search("已经发布")["results"][0]["document_id"] == ("product/new-guide")

    archived = knowledge_base.save_document(
        document_id="product/new-guide",
        title="新产品指南",
        summary="新产品的内部说明",
        tags=["product", "guide"],
        status="archived",
        body="# 新产品指南\n\n这是已经发布的内容。",
        expected_revision=published["revision"],
    )

    assert archived["status"] == "archived"
    assert knowledge_base.search("已经发布")["count"] == 0
    assert (
        knowledge_base.get_document("product/new-guide", include_inactive=True)["status"]
        == "archived"
    )


def test_agent_can_call_the_knowledge_search_tool(tmp_path: Path) -> None:
    knowledge_base = make_knowledge_base(tmp_path)
    captured: dict[str, object] = {}

    def knowledge_model(messages, info):
        del info
        tool_returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "search_knowledge"
        ]
        if tool_returns:
            captured["result"] = tool_returns[-1].content
            return ModelResponse(parts=[TextPart(content="退款通常在三个工作日内到账。")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="search_knowledge",
                    args={"query": "退款多久到账", "limit": 3},
                    tool_call_id="knowledge-search-1",
                )
            ]
        )

    agent = build_agent(
        Settings(ai_model="test", web_search_enabled=False, knowledge_enabled=True),
        model=FunctionModel(knowledge_model),
    )
    result = agent.run_sync(
        "退款多久到账？",
        deps=AgentDependencies(
            tenant_id="tenant-a",
            request_id="request-1",
            knowledge_base=knowledge_base,
        ),
    )

    assert result.output == "退款通常在三个工作日内到账。"
    tool_result = captured["result"]
    assert isinstance(tool_result, dict)
    assert tool_result["results"][0]["document_id"] == "product/refunds"


def test_knowledge_http_api_searches_and_reads_documents(tmp_path: Path) -> None:
    knowledge_base = make_knowledge_base(tmp_path)
    database_path = tmp_path / "knowledge-api.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        web_search_enabled=False,
        knowledge_dir=str(knowledge_base.root),
    )
    app = create_app(
        settings=settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    headers = {"X-Tenant-ID": "tenant-a"}

    with TestClient(app) as client:
        libraries = client.get("/v1/knowledge/libraries", headers=headers)
        sources = client.get(
            "/v1/knowledge/libraries/default/sources",
            headers=headers,
        )
        missing_library = client.get(
            "/v1/knowledge/libraries/missing/sources",
            headers=headers,
        )
        policy = client.get("/v1/knowledge/chunk-policy", headers=headers)
        index_status = client.get("/v1/knowledge/index-status", headers=headers)
        listed = client.get("/v1/knowledge/documents", headers=headers)
        searched = client.post(
            "/v1/knowledge/search",
            headers=headers,
            json={"query": "退款多久到账", "limit": 2},
        )
        outside_library = client.post(
            "/v1/knowledge/search",
            headers=headers,
            json={"query": "退款多久到账", "library_ids": ["other"]},
        )
        expanded = client.post(
            "/v1/knowledge/search",
            headers=headers,
            json={"query": "退款多久到账", "limit": 1, "include_context": True},
        )
        context = client.get(
            "/v1/knowledge/chunks/"
            f"{searched.json()['results'][0]['chunk_id']}/context",
            headers=headers,
        )
        read = client.get(
            "/v1/knowledge/documents/product/refunds?start_line=9&end_line=12",
            headers=headers,
        )

    assert libraries.status_code == 200
    assert libraries.json()[0]["library_id"] == "default"
    assert libraries.json()[0]["retrievable_document_count"] == 1
    assert sources.status_code == 200
    assert {source["source_id"] for source in sources.json()} == {
        "product/refunds",
        "drafts/future",
    }
    assert missing_library.status_code == 404
    assert policy.status_code == 200
    assert policy.json()["version"] == CHUNK_POLICY_VERSION
    assert policy.json()["hard_max_tokens"] == CHUNK_HARD_MAX_TOKENS
    assert index_status.status_code == 200
    assert index_status.json()["backend"] == "memory_lexical"
    assert index_status.json()["retrieval_modes"] == ["lexical"]
    assert index_status.json()["chunk_count"] > 0
    assert index_status.json()["embedding_model"] is None
    assert listed.status_code == 200
    assert listed.json()[0]["document_id"] == "product/refunds"
    assert searched.status_code == 200
    assert searched.json()["results"][0]["document_id"] == "product/refunds"
    assert searched.json()["results"][0]["library_id"] == "default"
    assert searched.json()["results"][0]["source_id"] == "product/refunds"
    assert searched.json()["chunk_policy_version"] == CHUNK_POLICY_VERSION
    assert searched.json()["results"][0]["heading_path"] == ["退款处理政策", "到账时间"]
    assert searched.json()["results"][0]["chunk_id"].startswith("kbch_")
    assert searched.json()["results"][0]["context"] is None
    assert searched.json()["results"][0]["context_citation"] is None
    assert expanded.status_code == 200
    assert expanded.json()["results"][0]["context_citation"]
    assert context.status_code == 200
    assert context.json()["document_id"] == "product/refunds"
    assert "三个工作日" in context.json()["context"]
    assert outside_library.status_code == 200
    assert outside_library.json()["results"] == []
    assert read.status_code == 200
    assert read.json()["source_uri"] == "kb://product/refunds"
    assert read.json()["library_id"] == "default"


def test_knowledge_management_api_saves_and_checks_revisions(tmp_path: Path) -> None:
    root = tmp_path / "managed-knowledge"
    database_path = tmp_path / "managed-knowledge.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        web_search_enabled=False,
        knowledge_dir=str(root),
    )
    app = create_app(
        settings=settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    headers = {"X-Tenant-ID": "tenant-a"}
    payload = {
        "document_id": "operations/on-call",
        "title": "值班手册",
        "summary": "值班人员使用",
        "tags": ["operations", "on-call"],
        "status": "draft",
        "body": "# 值班手册\n\n先确认告警来源。",
        "expected_revision": None,
    }

    with TestClient(app) as client:
        created = client.post(
            "/v1/knowledge/manage/documents",
            headers=headers,
            json=payload,
        )
        assert created.status_code == 201, created.text
        created_document = created.json()

        managed = client.get("/v1/knowledge/manage/documents", headers=headers)
        loaded = client.get(
            "/v1/knowledge/manage/documents/operations/on-call",
            headers=headers,
        )
        stale = client.put(
            "/v1/knowledge/manage/documents/operations/on-call",
            headers=headers,
            json={**payload, "title": "错误覆盖", "expected_revision": "0" * 64},
        )
        published = client.put(
            "/v1/knowledge/manage/documents/operations/on-call",
            headers=headers,
            json={
                **payload,
                "status": "active",
                "expected_revision": created_document["revision"],
            },
        )

    assert managed.status_code == 200
    assert managed.json()[0]["status"] == "draft"
    assert loaded.status_code == 200
    assert loaded.json()["body"].startswith("# 值班手册")
    assert stale.status_code == 409
    assert published.status_code == 200
    assert published.json()["status"] == "active"
