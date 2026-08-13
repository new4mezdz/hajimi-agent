from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.main import create_app
from agent_product.services.agent import AgentDependencies, build_agent
from agent_product.services.knowledge import KnowledgeBase


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


def test_document_status_and_tag_filters_are_enforced(tmp_path: Path) -> None:
    knowledge_base = make_knowledge_base(tmp_path)

    documents = knowledge_base.list_documents()
    matching = knowledge_base.search("退款", tags=["policy"])
    missing = knowledge_base.search("退款", tags=["architecture"])

    assert [document["document_id"] for document in documents] == ["product/refunds"]
    assert matching["count"] >= 1
    assert missing["results"] == []


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
    assert knowledge_base.search("已经发布")["results"][0]["document_id"] == (
        "product/new-guide"
    )

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
    assert knowledge_base.get_document(
        "product/new-guide", include_inactive=True
    )["status"] == "archived"


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
        listed = client.get("/v1/knowledge/documents", headers=headers)
        searched = client.post(
            "/v1/knowledge/search",
            headers=headers,
            json={"query": "退款多久到账", "limit": 2},
        )
        read = client.get(
            "/v1/knowledge/documents/product/refunds?start_line=9&end_line=12",
            headers=headers,
        )

    assert listed.status_code == 200
    assert listed.json()[0]["document_id"] == "product/refunds"
    assert searched.status_code == 200
    assert searched.json()["results"][0]["document_id"] == "product/refunds"
    assert read.status_code == 200
    assert read.json()["source_uri"] == "kb://product/refunds"


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
