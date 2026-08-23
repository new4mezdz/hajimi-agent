from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_product.schemas.chat import TokenUsage
from agent_product.services.knowledge_provider import KnowledgeProvider
from agent_product.services.skills import SkillProvider
from agent_product.services.support import SupportService
from agent_product.services.workspace import CodeWorkspace


@dataclass(slots=True)
class AgentDependencies:
    tenant_id: str
    request_id: str
    customer_id: str = "customer-demo-a"
    workspace: CodeWorkspace | None = None
    knowledge_base: KnowledgeProvider | None = None
    skills: SkillProvider | None = None
    support_service: SupportService | None = None
    profile_id: str = "unbound"


@dataclass(slots=True)
class AgentReply:
    output: str
    history_json: str
    usage: TokenUsage
    run_result: Any
