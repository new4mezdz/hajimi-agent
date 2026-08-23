from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent

from agent_product.core.config import Settings
from agent_product.services.agent_types import AgentDependencies
from agent_product.services.web_search import WebSearchClient


@dataclass(frozen=True, slots=True)
class CapabilityContext:
    settings: Settings
    web_search_active: bool = False
    web_search_client: WebSearchClient | None = None


PromptProvider = Callable[[CapabilityContext], str | None]
ToolRegistrar = Callable[[Agent, CapabilityContext], None]
CapabilityEnabled = Callable[[CapabilityContext], bool]


def always_enabled(context: CapabilityContext) -> bool:
    del context
    return True


@dataclass(frozen=True, slots=True)
class ToolPolicy:
    name: str
    category: str
    risk: str
    approval: str
    concurrency: str
    timeout_seconds: int

    def __post_init__(self) -> None:
        if self.risk not in {"read", "network", "write"}:
            raise ValueError(f"Unknown tool risk: {self.risk!r}")
        if self.approval not in {"automatic", "required"}:
            raise ValueError(f"Unknown approval mode: {self.approval!r}")
        if self.concurrency not in {"parallel", "exclusive"}:
            raise ValueError(f"Unknown concurrency mode: {self.concurrency!r}")
        if self.timeout_seconds <= 0:
            raise ValueError("Tool timeout must be positive")
        if (self.risk == "write") != (self.approval == "required"):
            raise ValueError("Write tools require approval; non-write tools must be automatic")

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "category": self.category,
            "risk": self.risk,
            "approval": self.approval,
            "concurrency": self.concurrency,
            "timeout_seconds": self.timeout_seconds,
        }


def tool_options(policy: ToolPolicy) -> dict[str, Any]:
    """Translate the single policy record into Pydantic AI enforcement options."""
    return {
        "requires_approval": policy.approval == "required",
        "sequential": policy.concurrency == "exclusive",
        "timeout": policy.timeout_seconds,
        "metadata": {"agent_product": policy.as_dict()},
    }


@dataclass(frozen=True, slots=True)
class CapabilityPack:
    id: str
    version: str
    order: int
    tools: tuple[ToolPolicy, ...]
    prompt: PromptProvider
    register: ToolRegistrar
    enabled: CapabilityEnabled = always_enabled


class CapabilityRegistry:
    def __init__(self, packs: tuple[CapabilityPack, ...]) -> None:
        self._packs = {pack.id: pack for pack in packs}
        if len(self._packs) != len(packs):
            raise ValueError("Capability Pack ids must be unique")
        tool_owners: dict[str, str] = {}
        for pack in packs:
            for policy in pack.tools:
                previous = tool_owners.setdefault(policy.name, pack.id)
                if previous != pack.id:
                    raise ValueError(
                        f"Tool {policy.name!r} is declared by both {previous!r} and {pack.id!r}"
                    )

    def resolve(self, pack_ids: tuple[str, ...]) -> tuple[CapabilityPack, ...]:
        missing = [pack_id for pack_id in pack_ids if pack_id not in self._packs]
        if missing:
            raise ValueError(f"Unknown Capability Pack(s): {', '.join(missing)}")
        return tuple(sorted((self._packs[pack_id] for pack_id in pack_ids), key=lambda p: p.order))

    def tool_catalog(self, pack_ids: tuple[str, ...]) -> tuple[ToolPolicy, ...]:
        return tuple(policy for pack in self.resolve(pack_ids) for policy in pack.tools)


def empty_prompt(context: CapabilityContext) -> None:
    del context
    return None


def no_tools(agent: Agent[AgentDependencies, object], context: CapabilityContext) -> None:
    del agent, context
