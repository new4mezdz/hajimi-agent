from pydantic import SecretStr
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.services.agent import _requires_web_search, _select_model, build_agent


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

    agent = build_agent(settings, model=TestModel(custom_output_text="Test response"))
    result = agent.run_sync("Hello")

    assert result.output == "Test response"


def test_explicit_and_current_prompts_require_web_search() -> None:
    assert _requires_web_search("帮我搜索 Python 最新稳定版本")
    assert _requires_web_search("What's the weather today?")


def test_timeless_prompt_does_not_require_web_search() -> None:
    assert not _requires_web_search("请解释一下依赖注入是什么")
