from __future__ import annotations

import json
from dataclasses import dataclass
from hashlib import sha256
from typing import Any

from pydantic_ai import Agent

from agent_product.capabilities import build_capability_registry
from agent_product.capabilities.base import ToolPolicy
from agent_product.core.config import Settings
from agent_product.services.agent import build_agent_composition
from agent_product.services.agent_profiles import AgentProfile, AgentProfileRegistry
from agent_product.services.knowledge_provider import (
    KnowledgeProvider,
    ScopedKnowledgeProvider,
)


@dataclass(frozen=True, slots=True)
class AgentRegistration:
    profile: AgentProfile
    agent: Agent
    active_capability_packs: tuple[str, ...]
    tool_catalog: tuple[ToolPolicy, ...]
    static_system_prompt: str
    prompt_hash: str
    tool_schemas: tuple[dict[str, Any], ...]
    tool_schema_hash: str
    composition_hash: str


class AgentRuntime:
    """Process-local registry of immutable, versioned Agent compositions."""

    def __init__(
        self,
        settings: Settings,
        profiles: AgentProfileRegistry,
        *,
        model: Any | None = None,
    ) -> None:
        self.settings = settings
        self.profiles = profiles
        capabilities = build_capability_registry()
        registrations: dict[str, AgentRegistration] = {}
        for profile in profiles.list():
            built = build_agent_composition(
                settings,
                model=model,
                profile=profile,
                capability_registry=capabilities,
            )
            packs = built.active_packs
            tool_catalog = tuple(policy for pack in packs for policy in pack.tools)
            registered_tools: dict[str, Any] = {}
            for toolset in built.agent.toolsets:
                for name, tool in getattr(toolset, "tools", {}).items():
                    if name in registered_tools:
                        raise ValueError(f"Agent registers tool {name!r} more than once")
                    registered_tools[name] = tool
            declared_names = {policy.name for policy in tool_catalog}
            if set(registered_tools) != declared_names:
                raise ValueError(
                    f"Agent Profile {profile.id!r} tool policy/catalog mismatch: "
                    f"declared={sorted(declared_names)!r}, "
                    f"registered={sorted(registered_tools)!r}"
                )
            tool_schemas: list[dict[str, Any]] = []
            for policy in tool_catalog:
                tool = registered_tools[policy.name]
                expected_metadata = {"agent_product": policy.as_dict()}
                if tool.metadata != expected_metadata:
                    raise ValueError(f"Tool {policy.name!r} metadata does not match its policy")
                if bool(tool.requires_approval) != (policy.approval == "required"):
                    raise ValueError(f"Tool {policy.name!r} approval enforcement drifted")
                if bool(tool.sequential) != (policy.concurrency == "exclusive"):
                    raise ValueError(f"Tool {policy.name!r} concurrency enforcement drifted")
                if tool.timeout != policy.timeout_seconds:
                    raise ValueError(f"Tool {policy.name!r} timeout enforcement drifted")
                tool_schemas.append(
                    {
                        "name": policy.name,
                        "description": tool.description,
                        "parameters": tool.function_schema.json_schema,
                        "returns": tool.function_schema.return_schema,
                    }
                )
            prompt_hash = sha256(built.static_instructions.encode("utf-8")).hexdigest()
            tool_schema_hash = sha256(
                json.dumps(
                    tool_schemas,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
            ).hexdigest()
            composition = {
                "profile_manifest_hash": profile.manifest_hash,
                "model": settings.ai_model,
                "prompt_hash": prompt_hash,
                "tool_schema_hash": tool_schema_hash,
                "packs": [{"id": pack.id, "version": pack.version} for pack in packs],
                "tools": [policy.as_dict() for policy in tool_catalog],
            }
            composition_hash = sha256(
                json.dumps(composition, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            registrations[profile.id] = AgentRegistration(
                profile=profile,
                agent=built.agent,
                active_capability_packs=tuple(pack.id for pack in packs),
                tool_catalog=tool_catalog,
                static_system_prompt=built.static_instructions,
                prompt_hash=prompt_hash,
                tool_schemas=tuple(tool_schemas),
                tool_schema_hash=tool_schema_hash,
                composition_hash=composition_hash,
            )
        self._registrations = registrations

    @property
    def default(self) -> AgentRegistration:
        return self.get(self.profiles.default_id)

    def get(self, profile_id: str | None = None) -> AgentRegistration:
        profile = self.profiles.get(profile_id)
        return self._registrations[profile.id]

    def get_bound(self, profile_id: str, version: str) -> AgentRegistration:
        profile = self.profiles.get_bound(profile_id, version)
        return self._registrations[profile.id]

    def list(self) -> tuple[AgentRegistration, ...]:
        return tuple(self._registrations[profile.id] for profile in self.profiles.list())

    @staticmethod
    def scope_knowledge(
        registration: AgentRegistration,
        provider: KnowledgeProvider,
    ) -> KnowledgeProvider | None:
        scope = registration.profile.knowledge_scope
        if scope is None:
            return None
        return ScopedKnowledgeProvider(provider, scope)
