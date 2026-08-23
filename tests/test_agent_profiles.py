import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart
from pydantic_ai.models.function import FunctionModel

from agent_product.core.config import Settings
from agent_product.main import create_app
from agent_product.services.agent import build_agent
from agent_product.services.agent_profiles import (
    AgentProfileError,
    build_builtin_profile_registry,
    build_profile_registry,
)
from agent_product.services.knowledge import KnowledgeBase
from agent_product.services.knowledge_provider import (
    KnowledgeScope,
    KnowledgeScopeError,
    ScopedKnowledgeProvider,
)


def _offered_tools(settings: Settings, profile_id: str) -> set[str]:
    offered: set[str] = set()

    def inspect_model(messages, info):
        del messages
        offered.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart(content="Tools inspected.")])

    profile = build_builtin_profile_registry(settings).get(profile_id)
    agent = build_agent(settings, model=FunctionModel(inspect_model), profile=profile)
    agent.run_sync("Inspect tools")
    return offered


def test_builtin_profiles_expose_distinct_capability_sets() -> None:
    settings = Settings(ai_model="test", web_search_enabled=False, knowledge_enabled=True)

    general = _offered_tools(settings, "general")
    knowledge = _offered_tools(settings, "knowledge")
    code = _offered_tools(settings, "code")
    support = _offered_tools(settings, "support")

    assert general == {"current_time", "calculator"}
    assert {"search_knowledge", "read_knowledge_document"} <= knowledge
    assert {"read_knowledge_context", "load_skill"} <= knowledge
    assert {"list_files", "read_file", "search_text"}.isdisjoint(knowledge)
    assert {
        "search_knowledge",
        "read_knowledge_document",
        "read_knowledge_context",
        "load_skill",
        "list_files",
        "read_file",
        "search_text",
        "create_file",
        "apply_patch",
        "write_file",
    } <= code
    assert {
        "search_knowledge",
        "read_knowledge_document",
        "find_my_orders",
        "lookup_my_order",
        "assess_after_sales_options",
        "create_support_case",
    } <= support
    assert {"list_files", "read_file", "apply_patch"}.isdisjoint(support)


def test_declarative_profile_can_specialize_existing_capabilities(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    (profile_dir / "support-custom.json").write_text(
        json.dumps(
            {
                "id": "support-custom",
                "version": "1",
                "display_name": "Support Agent",
                "description": "Support-tagged knowledge only",
                "capability_packs": ["common", "knowledge"],
                "permission_policy": "knowledge-read-only",
                "ui_features": ["chat", "knowledge"],
                "knowledge_scope": {
                    "scope_id": "support-only",
                    "required_tags": ["support"],
                    "library_ids": [],
                },
            }
        ),
        encoding="utf-8",
    )
    settings = Settings(
        ai_model="test",
        web_search_enabled=False,
        agent_profile_dir=str(profile_dir),
    )

    profile = build_profile_registry(settings).get("support-custom")

    assert profile.capability_packs == ("common", "knowledge")
    assert profile.knowledge_scope is not None
    assert profile.knowledge_scope.required_tags == ("support",)


def test_declarative_profile_rejects_unknown_fields(tmp_path: Path) -> None:
    profile_dir = tmp_path / "profiles"
    profile_dir.mkdir()
    (profile_dir / "unsafe.json").write_text(
        json.dumps(
            {
                "id": "unsafe",
                "version": "1",
                "display_name": "Unsafe",
                "description": "Invalid dynamic configuration",
                "capability_packs": ["common"],
                "permission_policy": "no-local-write",
                "python_module": "arbitrary.code",
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(AgentProfileError, match="unknown field"):
        build_profile_registry(
            Settings(agent_profile_dir=str(profile_dir), web_search_enabled=False)
        )


def test_knowledge_scope_filters_search_and_direct_document_reads(tmp_path: Path) -> None:
    root = tmp_path / "knowledge"
    root.mkdir()
    (root / "public.md").write_text(
        """---
id: public
title: Public guide
tags: [public]
status: active
---

# Public guide

Public answer.
""",
        encoding="utf-8",
    )
    (root / "private.md").write_text(
        """---
id: private
title: Private guide
tags: [private]
status: active
---

# Private guide

Private answer.
""",
        encoding="utf-8",
    )
    provider = ScopedKnowledgeProvider(
        KnowledgeBase(root),
        KnowledgeScope(
            scope_id="public-only",
            required_tags=("public",),
            library_ids=("default",),
        ),
    )

    result = provider.search("answer", limit=10)

    assert [hit["document_id"] for hit in result["results"]] == ["public"]
    assert provider.read_context(result["results"][0]["chunk_id"])["document_id"] == "public"
    assert provider.read_document("public")["document_id"] == "public"
    with pytest.raises(KnowledgeScopeError):
        provider.read_document("private")
    private_hit = provider.provider.search("Private answer")["results"][0]
    with pytest.raises(KnowledgeScopeError):
        provider.read_context(private_hit["chunk_id"])

    unavailable_library = ScopedKnowledgeProvider(
        KnowledgeBase(root),
        KnowledgeScope(scope_id="other-library", library_ids=("other",)),
    )
    assert unavailable_library.search("answer")["results"] == []
    with pytest.raises(KnowledgeScopeError):
        unavailable_library.read_document("public")


def test_profile_api_and_conversation_binding(tmp_path: Path) -> None:
    database_path = tmp_path / "profiles.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        web_search_enabled=False,
    )
    app = create_app(
        settings=settings,
        model=FunctionModel(
            lambda messages, info: ModelResponse(parts=[TextPart(content="Profile response")])
        ),
    )
    headers = {"X-Tenant-ID": "tenant-a"}

    with TestClient(app) as client:
        profiles = client.get("/v1/agent-profiles", headers=headers)
        created = client.post(
            "/v1/chat",
            headers=headers,
            json={"message": "Use knowledge", "profile_id": "knowledge"},
        )
        conversation_id = created.json()["conversation_id"]
        continued = client.post(
            "/v1/chat",
            headers=headers,
            json={"message": "Continue", "conversation_id": conversation_id},
        )
        switched = client.post(
            "/v1/chat",
            headers=headers,
            json={
                "message": "Switch",
                "conversation_id": conversation_id,
                "profile_id": "code",
            },
        )
        metadata = client.get(
            f"/v1/conversations/{conversation_id}",
            headers=headers,
        )

    assert profiles.status_code == 200
    assert {profile["id"] for profile in profiles.json()} == {
        "general",
        "knowledge",
        "code",
        "support",
    }
    code_profile = next(profile for profile in profiles.json() if profile["id"] == "code")
    assert code_profile["is_default"]
    assert code_profile["composition_hash"]
    assert code_profile["prompt_hash"]
    assert code_profile["tool_schema_hash"]
    assert "web" not in code_profile["active_capability_packs"]
    assert next(tool for tool in code_profile["tools"] if tool["name"] == "apply_patch")[
        "approval"
    ] == "required"
    assert created.status_code == 200
    assert created.json()["profile_id"] == "knowledge"
    assert continued.status_code == 200
    assert continued.json()["profile_id"] == "knowledge"
    assert switched.status_code == 409
    assert metadata.json()["profile_id"] == "knowledge"
    assert metadata.json()["profile_version"] == "2"


def test_streaming_profile_header_is_bound_to_the_new_conversation(
    client: TestClient,
) -> None:
    conversation_id = str(uuid4())
    payload = {
        "trigger": "submit-message",
        "id": conversation_id,
        "messages": [
            {
                "id": "profile-message-1",
                "role": "user",
                "parts": [{"type": "text", "text": "Hello"}],
            }
        ],
    }
    headers = {
        "X-Tenant-ID": "tenant-a",
        "X-Agent-Profile": "general",
        "Accept": "text/event-stream",
    }

    created = client.post("/v1/chat/stream", headers=headers, json=payload)
    metadata = client.get(
        f"/v1/conversations/{conversation_id}",
        headers={"X-Tenant-ID": "tenant-a"},
    )
    switched = client.post(
        "/v1/chat/stream",
        headers={**headers, "X-Agent-Profile": "code"},
        json={
            **payload,
            "messages": [
                {
                    "id": "profile-message-2",
                    "role": "user",
                    "parts": [{"type": "text", "text": "Switch"}],
                }
            ],
        },
    )

    assert created.status_code == 200
    assert metadata.json()["profile_id"] == "general"
    assert switched.status_code == 409
