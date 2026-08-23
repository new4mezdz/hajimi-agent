from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_product.core.security import require_api_key
from agent_product.schemas.settings import AgentSettingsInput, AgentSettingsResponse
from agent_product.services.agent_profiles import build_profile_registry
from agent_product.services.agent_runtime import AgentRuntime
from agent_product.services.local_settings import LocalSettingsError

router = APIRouter(
    prefix="/v1/settings",
    tags=["settings"],
    dependencies=[Depends(require_api_key)],
)


def _store(request: Request):
    store = request.app.state.local_settings_store
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Local settings are not available in this deployment",
        )
    client_host = request.client.host if request.client else None
    if client_host not in {"127.0.0.1", "::1", "testclient"}:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Local settings are only available from this device",
        )
    return store


@router.get("", response_model=AgentSettingsResponse, response_model_by_alias=True)
def get_local_settings(request: Request) -> dict:
    try:
        return _store(request).public(request.app.state.base_settings)
    except LocalSettingsError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=str(exc),
        ) from exc


@router.put("", response_model=AgentSettingsResponse, response_model_by_alias=True)
def save_local_settings(request: Request, input: AgentSettingsInput) -> dict:
    store = _store(request)
    try:
        settings = store.update(
            request.app.state.base_settings,
            provider=input.provider,
            model=input.model.strip(),
            web_search_enabled=input.web_search_enabled,
            workspace_write_enabled=input.workspace_write_enabled,
            agent_instructions=input.agent_instructions,
            api_key=input.api_key,
            clear_api_key=input.clear_api_key,
        )
        profiles = build_profile_registry(settings)
        runtime = AgentRuntime(
            settings,
            profiles,
            model=request.app.state.model_override,
        )
    except (LocalSettingsError, ValueError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc

    request.app.state.settings = settings
    request.app.state.agent_runtime = runtime
    request.app.state.agent = runtime.default.agent
    return store.public(request.app.state.base_settings)
