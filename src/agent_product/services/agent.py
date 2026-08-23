import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, DeferredToolRequests, ModelMessage, RunContext
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.providers.anthropic import AnthropicProvider

from agent_product.capabilities import build_capability_registry
from agent_product.capabilities.base import (
    CapabilityContext,
    CapabilityPack,
    CapabilityRegistry,
)
from agent_product.core.config import Settings
from agent_product.schemas.chat import TokenUsage
from agent_product.services.agent_profiles import (
    AgentProfile,
    build_builtin_profile_registry,
)
from agent_product.services.agent_types import AgentDependencies, AgentReply
from agent_product.services.web_search import DeepSeekWebSearchClient, WebSearchClient

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BuiltAgent:
    agent: Agent
    active_packs: tuple[CapabilityPack, ...]
    static_instructions: str


def _deepseek_model_name(model_name: str) -> str | None:
    provider, separator, name = model_name.partition(":")
    if separator and provider == "deepseek":
        return name
    return None


def _select_model(settings: Settings) -> tuple[Any, bool]:
    """Select DeepSeek's Anthropic-compatible API when server search is available."""
    deepseek_model = _deepseek_model_name(settings.ai_model)
    if not settings.web_search_enabled or deepseek_model is None:
        return settings.ai_model, False

    if not deepseek_model.startswith("deepseek-v4-"):
        logger.warning(
            "DeepSeek native web search requires a V4 model; "
            "using the configured model without search",
            extra={"model": settings.ai_model},
        )
        return settings.ai_model, False

    if settings.deepseek_api_key is None:
        logger.warning(
            "DeepSeek web search is enabled but DEEPSEEK_API_KEY is not configured; "
            "using the configured model without search"
        )
        return settings.ai_model, False

    provider = AnthropicProvider(
        api_key=settings.deepseek_api_key.get_secret_value(),
        base_url=settings.deepseek_anthropic_base_url,
    )
    return AnthropicModel(deepseek_model, provider=provider), True


def _prompt_text(prompt: str | Sequence[Any] | None) -> str:
    if isinstance(prompt, str):
        return prompt
    if prompt is None:
        return ""
    return " ".join(str(part) for part in prompt)


def _requires_web_search(prompt: str | Sequence[Any] | None) -> bool:
    text = _prompt_text(prompt).casefold()
    markers = (
        "联网",
        "上网",
        "搜索",
        "搜一下",
        "查一下",
        "查找",
        "最新",
        "今天",
        "当前",
        "实时",
        "近期",
        "最近",
        "新闻",
        "价格",
        "天气",
        "search",
        "look up",
        "latest",
        "current",
        "today",
        "real-time",
        "recent",
        "news",
        "price",
        "weather",
    )
    return any(marker in text for marker in markers)


def build_agent_composition(
    settings: Settings,
    model: Any | None = None,
    web_search_client: WebSearchClient | None = None,
    *,
    profile: AgentProfile | None = None,
    capability_registry: CapabilityRegistry | None = None,
) -> BuiltAgent:
    active_profile = profile or build_builtin_profile_registry(settings).get()
    registry = capability_registry or build_capability_registry()
    packs = registry.resolve(active_profile.capability_packs)
    has_web_capability = any(pack.id == "web" for pack in packs)
    selected_model, web_search_active = (
        (model, False) if model is not None else _select_model(settings)
    )
    web_search_active = web_search_active and has_web_capability
    if web_search_active and web_search_client is None:
        deepseek_model = _deepseek_model_name(settings.ai_model)
        assert deepseek_model is not None
        assert settings.deepseek_api_key is not None
        web_search_client = DeepSeekWebSearchClient(
            api_key=settings.deepseek_api_key.get_secret_value(),
            base_url=settings.deepseek_anthropic_base_url,
            model_name=deepseek_model,
            max_uses=settings.web_search_max_uses,
        )

    capability_context = CapabilityContext(
        settings=settings,
        web_search_active=web_search_active,
        web_search_client=web_search_client,
    )
    active_packs = tuple(pack for pack in packs if pack.enabled(capability_context))
    prompt_sections = [
        section
        for pack in active_packs
        if (section := pack.prompt(capability_context)) is not None
    ]
    instructions = active_profile.persona or settings.agent_instructions
    if prompt_sections:
        instructions = f"{instructions}\n\n" + "\n\n".join(prompt_sections)

    def model_settings(ctx: RunContext[AgentDependencies]) -> dict[str, Any]:
        if web_search_active and ctx.run_step == 1 and _requires_web_search(ctx.prompt):
            return {"tool_choice": ["web_search"]}
        return {"tool_choice": "auto"}

    agent = Agent(
        selected_model,
        deps_type=AgentDependencies,
        output_type=[str, DeferredToolRequests],
        instructions=instructions,
        model_settings=model_settings,
        # Keep health checks and documentation available before a provider key is configured.
        # Pydantic AI will validate the provider when the first agent run starts.
        defer_model_check=model is None,
    )

    for pack in active_packs:
        pack.register(agent, capability_context)

    return BuiltAgent(
        agent=agent,
        active_packs=active_packs,
        static_instructions=instructions,
    )


def build_agent(
    settings: Settings,
    model: Any | None = None,
    web_search_client: WebSearchClient | None = None,
    *,
    profile: AgentProfile | None = None,
    capability_registry: CapabilityRegistry | None = None,
) -> Agent:
    return build_agent_composition(
        settings,
        model,
        web_search_client,
        profile=profile,
        capability_registry=capability_registry,
    ).agent


async def run_agent(
    agent: Agent,
    prompt: str,
    history: list[ModelMessage],
    conversation_id: str,
    dependencies: AgentDependencies,
) -> AgentReply:
    result = await agent.run(
        prompt,
        deps=dependencies,
        message_history=history,
        conversation_id=conversation_id,
    )
    raw_usage = result.usage
    usage = TokenUsage(
        requests=getattr(raw_usage, "requests", 0) or 0,
        input_tokens=getattr(raw_usage, "input_tokens", 0) or 0,
        output_tokens=getattr(raw_usage, "output_tokens", 0) or 0,
    )
    return AgentReply(
        output=str(result.output),
        history_json=result.all_messages_json().decode("utf-8"),
        usage=usage,
        run_result=result,
    )
