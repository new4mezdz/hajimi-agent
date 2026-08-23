from fastapi import APIRouter, Depends, Request

from agent_product.core.security import require_api_key
from agent_product.schemas.models import AgentModelResponse

router = APIRouter(
    prefix="/v1/models",
    tags=["models"],
    dependencies=[Depends(require_api_key)],
)


@router.get("", response_model=list[AgentModelResponse])
async def list_configured_models(request: Request) -> list[AgentModelResponse]:
    """Expose only models whose active provider has a usable credential."""
    settings = request.app.state.settings
    provider, separator, _ = settings.ai_model.partition(":")
    if not separator:
        return []

    credentials = {
        "openai": settings.openai_api_key,
        "deepseek": settings.deepseek_api_key,
        "anthropic": settings.anthropic_api_key,
    }
    credential = credentials.get(provider)
    if credential is None or not credential.get_secret_value().strip():
        return []

    return [
        AgentModelResponse(
            provider=provider,
            model=settings.ai_model,
        )
    ]
