from typing import Annotated

from fastapi import APIRouter, Depends, Request

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.schemas.profiles import AgentProfileResponse, KnowledgeScopeResponse

router = APIRouter(
    prefix="/v1/agent-profiles",
    tags=["agent-profiles"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=list[AgentProfileResponse])
async def list_agent_profiles(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> list[AgentProfileResponse]:
    del tenant_id
    runtime = request.app.state.agent_runtime
    registry = runtime.profiles
    return [
        AgentProfileResponse(
            # Registration is the effective runtime composition; Profile is its manifest.
            id=registration.profile.id,
            version=registration.profile.version,
            display_name=registration.profile.display_name,
            description=registration.profile.description,
            capability_packs=list(registration.profile.capability_packs),
            active_capability_packs=list(registration.active_capability_packs),
            permission_policy=registration.profile.permission_policy,
            ui_features=list(registration.profile.ui_features),
            knowledge_scope=(
                KnowledgeScopeResponse(
                    scope_id=registration.profile.knowledge_scope.scope_id,
                    required_tags=list(registration.profile.knowledge_scope.required_tags),
                    library_ids=list(registration.profile.knowledge_scope.library_ids),
                )
                if registration.profile.knowledge_scope
                else None
            ),
            manifest_hash=registration.profile.manifest_hash,
            composition_hash=registration.composition_hash,
            prompt_hash=registration.prompt_hash,
            tool_schema_hash=registration.tool_schema_hash,
            tools=[policy.as_dict() for policy in registration.tool_catalog],
            is_default=registration.profile.id == registry.default_id,
        )
        for registration in runtime.list()
    ]
