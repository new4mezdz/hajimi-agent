from pathlib import Path

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
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.services.agent import (
    AgentDependencies,
    _requires_web_search,
    _select_model,
    build_agent,
)
from agent_product.services.workspace import WorkspaceRegistry


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
    assert {"list_files", "read_file", "search_text"} <= offered_tools
    assert "write_file" not in offered_tools
