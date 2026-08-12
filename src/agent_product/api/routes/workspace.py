from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.schemas.workspace import WorkspaceCreateRequest, WorkspaceResponse
from agent_product.services.workspace import WorkspaceError, WorkspaceNotFoundError

router = APIRouter(
    prefix="/v1/workspaces",
    tags=["workspace"],
    dependencies=[Depends(require_api_key)],
)


def _response(workspace) -> WorkspaceResponse:
    return WorkspaceResponse(
        id=workspace.id,
        name=workspace.name,
        path=str(workspace.root),
    )


@router.post("", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    body: WorkspaceCreateRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> WorkspaceResponse:
    try:
        workspace = request.app.state.workspace_registry.create(body.path, tenant_id)
    except WorkspaceError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return _response(workspace)


@router.get("/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> WorkspaceResponse:
    try:
        workspace = request.app.state.workspace_registry.get(workspace_id, tenant_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    assert workspace is not None
    return _response(workspace)
