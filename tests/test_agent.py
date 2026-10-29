import json
from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr
from pydantic_ai import (
    DeferredToolRequests,
    DeferredToolResults,
    ModelResponse,
    TextPart,
    ToolCallPart,
)
from pydantic_ai.messages import ToolReturnPart
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.function import FunctionModel
from pydantic_ai.models.openai import OpenAIResponsesModel
from pydantic_ai.models.test import TestModel

from agent_product.core.config import DEFAULT_AI_MODEL, Settings
from agent_product.services.agent import (
    AgentDependencies,
    _requires_web_search,
    _select_model,
    build_agent,
)
from agent_product.services.workspace import WorkspaceRegistry


def test_default_openai_model_defers_missing_credentials(monkeypatch) -> None:
    monkeypatch.delenv("AI_MODEL", raising=False)
    settings = Settings(_env_file=None, openai_api_key=None)

    selected_model, web_search_active = _select_model(settings)

    assert settings.ai_model == "openai:gpt-6.1-sol"
    assert selected_model == "openai-responses:gpt-6.1-sol"
    assert web_search_active is False


@pytest.mark.asyncio
async def test_sol_uses_platform_key_and_responses_for_tool_round_trip(monkeypatch) -> None:
    requests: list[dict] = []

    def respond(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.openai.com/v1/responses"
        assert request.headers["authorization"] == "Bearer test-platform-key"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "gpt-6.1-sol"
        assert any(tool.get("name") == "calculator" for tool in body["tools"])
        if len(requests) == 1:
            output = [{
                "id": "fc_calculator",
                "type": "function_call",
                "call_id": "call_calculator",
                "name": "calculator",
                "arguments": '{"expression":"2+2"}',
                "status": "completed",
            }]
        else:
            tool_result = next(
                item for item in body["input"] if item.get("type") == "function_call_output"
            )
            assert tool_result["call_id"] == "call_calculator"
            assert json.loads(tool_result["output"])["result"] == 4
            output = [{
                "id": "msg_result",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": "4", "annotations": []}],
            }]
        return httpx.Response(200, json={
            "id": f"resp_{len(requests)}",
            "object": "response",
            "created_at": 1,
            "model": "gpt-6.1-sol",
            "status": "completed",
            "output": output,
            "usage": {"input_tokens": 20, "output_tokens": 4, "total_tokens": 24},
        })

    monkeypatch.setenv("OPENAI_API_KEY", "unused-environment-key")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://unused.example/v1")
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as http_client:
        monkeypatch.setattr(
            "pydantic_ai.providers.openai.create_async_http_client", lambda: http_client
        )
        agent = build_agent(Settings(
            _env_file=None,
            ai_model=DEFAULT_AI_MODEL,
            openai_api_key=SecretStr("test-platform-key"),
        ))

        result = await agent.run("Calculate 2+2")

    assert isinstance(agent.model, OpenAIResponsesModel)
    assert result.output == "4"
    assert len(requests) == 2


def test_deepseek_v4_uses_anthropic_model_for_native_search() -> None:
    settings = Settings(
        ai_model="deepseek:deepseek-v4-flash",
        deepseek_api_key=SecretStr("test-key"),
        web_search_enabled=True,
    )

    selected_model, web_search_active = _select_model(settings)

    assert isinstance(selected_model, AnthropicModel)
    assert web_search_active is True
    assert selected_model.model_name == "deepseek-v4-flash"


def test_web_search_can_be_disabled() -> None:
    settings = Settings(
        ai_model="deepseek:deepseek-v4-flash",
        deepseek_api_key=SecretStr("test-key"),
        web_search_enabled=False,
    )

    selected_model, web_search_active = _select_model(settings)

    assert selected_model == settings.ai_model
    assert web_search_active is False


def test_injected_test_model_does_not_enable_native_search() -> None:
    settings = Settings(
        ai_model="deepseek:deepseek-v4-flash",
        deepseek_api_key=SecretStr("test-key"),
        web_search_enabled=True,
    )

    agent = build_agent(
        settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    result = agent.run_sync("Hello")

    assert result.output == "Test response"


def test_explicit_and_current_prompts_require_web_search() -> None:
    assert _requires_web_search("帮我搜索 Python 最新稳定版本")
    assert _requires_web_search("What's the weather today?")


def test_timeless_prompt_does_not_require_web_search() -> None:
    assert not _requires_web_search("请解释一下依赖注入是什么")


def test_write_tool_pauses_until_explicit_approval(tmp_path: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    workspace = WorkspaceRegistry().create(str(root), "tenant-a")

    def write_model(messages, info):
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(parts=[TextPart(content="The approved file was written.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name="write_file",
                    args={"path": "notes/approved.txt", "content": "approved\n"},
                    tool_call_id="write-approval-1",
                )
            ]
        )

    agent = build_agent(
        Settings(ai_model="test", web_search_enabled=False),
        model=FunctionModel(write_model),
    )
    dependencies = AgentDependencies(
        tenant_id="tenant-a",
        request_id="request-1",
        workspace=workspace,
    )

    first = agent.run_sync("Create the file", deps=dependencies)
    assert isinstance(first.output, DeferredToolRequests)
    assert not (root / "notes" / "approved.txt").exists()

    approval = DeferredToolResults(
        approvals={first.output.approvals[0].tool_call_id: True}
    )
    resumed = agent.run_sync(
        "Continue after approval",
        deps=dependencies,
        message_history=first.all_messages(),
        deferred_tool_results=approval,
    )

    assert resumed.output == "The approved file was written."
    assert (root / "notes" / "approved.txt").read_text(encoding="utf-8") == "approved\n"


@pytest.mark.parametrize(
    ("tool_name", "arguments", "target", "expected"),
    [
        (
            "create_file",
            {"path": "created.txt", "content": "created\n"},
            "created.txt",
            "created\n",
        ),
        (
            "apply_patch",
            {
                "path": "source.txt",
                "old_text": "before\n",
                "new_text": "after\n",
            },
            "source.txt",
            "after\n",
        ),
    ],
)
def test_new_workspace_write_tools_require_approval(
    tmp_path: Path,
    tool_name: str,
    arguments: dict[str, str],
    target: str,
    expected: str,
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    (root / "source.txt").write_bytes(b"before\n")
    workspace = WorkspaceRegistry().create(str(root), "tenant-a")

    def tool_model(messages, info):
        del info
        if any(
            isinstance(part, ToolReturnPart)
            for message in messages
            for part in message.parts
        ):
            return ModelResponse(parts=[TextPart(content="Workspace change completed.")])
        return ModelResponse(
            parts=[
                ToolCallPart(
                    tool_name=tool_name,
                    args=arguments,
                    tool_call_id=f"{tool_name}-approval-1",
                )
            ]
        )

    agent = build_agent(
        Settings(ai_model="test", web_search_enabled=False),
        model=FunctionModel(tool_model),
    )
    dependencies = AgentDependencies(
        tenant_id="tenant-a",
        request_id="request-1",
        workspace=workspace,
    )

    first = agent.run_sync("Change the workspace", deps=dependencies)
    assert isinstance(first.output, DeferredToolRequests)
    if tool_name == "create_file":
        assert not (root / target).exists()
    else:
        assert (root / target).read_text(encoding="utf-8") == "before\n"

    approval = DeferredToolResults(
        approvals={first.output.approvals[0].tool_call_id: True}
    )
    resumed = agent.run_sync(
        "Continue after approval",
        deps=dependencies,
        message_history=first.all_messages(),
        deferred_tool_results=approval,
    )

    assert resumed.output == "Workspace change completed."
    assert (root / target).read_text(encoding="utf-8") == expected


def test_agent_offers_recommended_base_tools() -> None:
    offered_tools: set[str] = set()

    def inspect_model(messages, info):
        del messages
        offered_tools.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart(content="Tools inspected.")])

    agent = build_agent(
        Settings(ai_model="test", web_search_enabled=False),
        model=FunctionModel(inspect_model),
    )

    result = agent.run_sync("Inspect available tools")

    assert result.output == "Tools inspected."
    assert {
        "list_files",
        "read_file",
        "search_text",
        "calculator",
        "create_file",
        "apply_patch",
    } <= offered_tools


def test_read_only_mode_does_not_offer_write_tool() -> None:
    offered_tools: set[str] = set()

    def read_only_model(messages, info):
        del messages
        offered_tools.update(tool.name for tool in info.function_tools)
        return ModelResponse(parts=[TextPart(content="Read-only mode is active.")])

    agent = build_agent(
        Settings(
            ai_model="test",
            web_search_enabled=False,
            workspace_write_enabled=False,
        ),
        model=FunctionModel(read_only_model),
    )

    result = agent.run_sync("Inspect the workspace")

    assert result.output == "Read-only mode is active."
    assert {"list_files", "read_file", "search_text", "calculator"} <= offered_tools
    assert {"write_file", "create_file", "apply_patch"}.isdisjoint(offered_tools)
