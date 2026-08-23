from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai import ModelResponse, TextPart, ToolCallPart
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.main import create_app
from agent_product.services.agent import build_agent
from agent_product.services.agent_profiles import build_builtin_profile_registry
from agent_product.services.agent_types import AgentDependencies
from agent_product.services.conversation_events import completed_run_events
from agent_product.services.skills import LocalSkillRegistry, SkillNotFoundError


def write_skill(root: Path, name: str, metadata: str, body: str) -> Path:
    path = root / name / "SKILL.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{metadata}\n---\n\n{body}\n", encoding="utf-8")
    return path


def test_skill_catalog_filters_status_profile_and_loads_fresh_body(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    incident = write_skill(
        root,
        "incident-response",
        """name: incident-response
description: >-
  Diagnose production incidents and prepare a bounded recovery plan.
version: 1
status: published
profiles: [code]
tags: [operations, safety]""",
        "# Incident response\n\n1. Confirm impact.\n2. Collect evidence.",
    )
    write_skill(
        root,
        "draft-procedure",
        """name: draft-procedure
description: Hidden draft
status: draft""",
        "# Draft",
    )
    registry = LocalSkillRegistry(root)

    code_catalog = registry.scoped("code").list()
    knowledge_catalog = registry.scoped("knowledge").list()

    assert [summary.name for summary in code_catalog] == ["incident-response"]
    assert code_catalog[0].description.startswith("Diagnose production incidents")
    assert knowledge_catalog == ()
    loaded = registry.scoped("code").get("incident-response")
    assert "Collect evidence" in loaded.content
    assert loaded.resource_base == "incident-response"

    incident.write_text(
        incident.read_text(encoding="utf-8").replace(
            "2. Collect evidence.", "2. Collect current evidence."
        ),
        encoding="utf-8",
    )
    refreshed = registry.scoped("code").get("incident-response")
    assert "Collect current evidence" in refreshed.content
    assert refreshed.summary.revision != loaded.summary.revision

    try:
        registry.scoped("knowledge").get("incident-response")
    except SkillNotFoundError:
        pass
    else:
        raise AssertionError("Profile-scoped Skill loading must fail closed")


def test_agent_receives_skill_catalog_and_can_load_selected_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(
        root,
        "incident-response",
        """name: incident-response
description: Diagnose production incidents.
status: published
profiles: [code]""",
        "# Workflow\n\nConfirm the impact before changing anything.",
    )
    captured: dict[str, object] = {}

    def skill_model(messages, info):
        del info
        captured.setdefault("first_request", repr(messages))
        tool_returns = [
            part
            for message in messages
            for part in message.parts
            if isinstance(part, ToolReturnPart) and part.tool_name == "load_skill"
        ]
        if tool_returns:
            captured["skill"] = tool_returns[-1].content
            return ModelResponse(parts=[TextPart(content="Skill loaded.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="load_skill",
                    args={"name": "incident-response"},
                    tool_call_id="skill-load-1",
                )
            ]
        )

    settings = Settings(
        ai_model="test",
        web_search_enabled=False,
        agent_profile_dir=None,
    )
    profile = build_builtin_profile_registry(settings).get("code")
    agent = build_agent(settings, model=FunctionModel(skill_model), profile=profile)
    result = agent.run_sync(
        "Handle the incident",
        deps=AgentDependencies(
            tenant_id="tenant-a",
            request_id="request-1",
            profile_id="code",
            skills=LocalSkillRegistry(root).scoped("code"),
        ),
    )

    assert result.output == "Skill loaded."
    assert "<available_skills>" in str(captured["first_request"])
    assert "incident-response" in str(captured["first_request"])
    assert "Confirm the impact" not in str(captured["first_request"])
    assert isinstance(captured["skill"], dict)
    assert "Confirm the impact" in captured["skill"]["content"]


def test_skill_api_uses_agent_profile_scope(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(
        root,
        "incident-response",
        """name: incident-response
description: Diagnose production incidents.
status: published
profiles: [code]""",
        "# Workflow\n\nConfirm impact.",
    )
    settings = Settings(
        app_env="test",
        ai_model="test",
        agent_profile_dir=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'skills.db').as_posix()}",
        web_search_enabled=False,
        skills_dir=str(root),
    )
    app = create_app(
        settings=settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    headers = {"X-Tenant-ID": "tenant-a"}

    with TestClient(app) as client:
        code_catalog = client.get("/v1/skills?profile_id=code", headers=headers)
        knowledge_catalog = client.get(
            "/v1/skills?profile_id=knowledge", headers=headers
        )
        loaded = client.get(
            "/v1/skills/incident-response?profile_id=code", headers=headers
        )

    assert code_catalog.status_code == 200
    assert code_catalog.json()[0]["name"] == "incident-response"
    assert knowledge_catalog.json() == []
    assert loaded.status_code == 200
    assert "Confirm impact" in loaded.json()["content"]


def test_installed_ecommerce_skills_are_support_scoped() -> None:
    root = Path(__file__).parents[1] / "skills"
    registry = LocalSkillRegistry(root)

    support_names = {summary.name for summary in registry.scoped("support").list()}
    code_names = {summary.name for summary in registry.scoped("code").list()}

    assert support_names == {
        "order-delivery-status",
        "after-sales-resolution",
        "refund-exception-review",
        "delivery-exception-triage",
    }
    assert not support_names & code_names
    loaded = registry.scoped("support").get("after-sales-resolution")
    assert "find_my_orders" in loaded.content
    assert "assess_after_sales_options" in loaded.content
    assert "create_support_case" in loaded.content


def test_loaded_skill_produces_auditable_skill_event() -> None:
    root = Path(__file__).parents[1] / "skills"
    captured_result = None

    def skill_model(messages, info):
        del info
        if any(
            isinstance(part, ToolReturnPart) and part.tool_name == "load_skill"
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(parts=[TextPart(content="Skill workflow loaded.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="load_skill",
                    args={"name": "after-sales-resolution"},
                    tool_call_id="support-skill-load-1",
                )
            ]
        )

    settings = Settings(ai_model="test", web_search_enabled=False, agent_profile_dir=None)
    profile = build_builtin_profile_registry(settings).get("support")
    agent = build_agent(settings, model=FunctionModel(skill_model), profile=profile)
    captured_result = agent.run_sync(
        "我买的键盘坏了",
        deps=AgentDependencies(
            tenant_id="tenant-a",
            customer_id="customer-demo-a",
            request_id="request-1",
            profile_id="support",
            skills=LocalSkillRegistry(root).scoped("support"),
        ),
    )

    events = completed_run_events("turn-skill-1", captured_result)
    skill_event = next(payload for kind, payload in events if kind == "skill.loaded")
    assert skill_event["name"] == "after-sales-resolution"
    assert skill_event["version"] == "1"
    assert skill_event["revision"]
