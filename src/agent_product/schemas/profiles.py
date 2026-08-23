from pydantic import BaseModel


class KnowledgeScopeResponse(BaseModel):
    scope_id: str
    required_tags: list[str]
    library_ids: list[str]
    agent_access: str = "read-only"


class AgentProfileResponse(BaseModel):
    id: str
    version: str
    display_name: str
    description: str
    capability_packs: list[str]
    active_capability_packs: list[str]
    permission_policy: str
    ui_features: list[str]
    knowledge_scope: KnowledgeScopeResponse | None
    manifest_hash: str
    composition_hash: str
    prompt_hash: str
    tool_schema_hash: str
    tools: list[dict[str, object]]
    is_default: bool
