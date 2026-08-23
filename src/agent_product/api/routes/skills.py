from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.schemas.skills import SkillDefinitionResponse, SkillSummaryResponse
from agent_product.services.agent_profiles import AgentProfileError
from agent_product.services.skills import SkillNotFoundError

router = APIRouter(
    prefix="/v1/skills",
    tags=["skills"],
    dependencies=[Depends(require_api_key)],
)


def _profile_provider(request: Request, profile_id: str | None):
    requested = profile_id or request.headers.get("X-Agent-Profile")
    try:
        registration = request.app.state.agent_runtime.get(requested)
    except AgentProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    if "skills" not in registration.active_capability_packs:
        return None
    return request.app.state.skill_registry.scoped(registration.profile.id)


@router.get("", response_model=list[SkillSummaryResponse])
async def list_skills(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    profile_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> list[SkillSummaryResponse]:
    del tenant_id
    provider = _profile_provider(request, profile_id)
    if provider is None:
        return []
    return [SkillSummaryResponse.model_validate(summary.as_dict()) for summary in provider.list()]


@router.get("/{name}", response_model=SkillDefinitionResponse)
async def get_skill(
    name: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    profile_id: str | None = Query(default=None, min_length=1, max_length=64),
) -> SkillDefinitionResponse:
    del tenant_id
    provider = _profile_provider(request, profile_id)
    if provider is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Skill {name!r} is not available in this Agent Profile",
        )
    try:
        definition = provider.get(name)
    except SkillNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return SkillDefinitionResponse.model_validate(definition.as_dict())
