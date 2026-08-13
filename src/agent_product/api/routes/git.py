import asyncio
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.schemas.git import (
    GitCommitPrepareRequest,
    GitCommitResponse,
    GitConfirmationResponse,
    GitConfirmRequest,
    GitPushResponse,
    GitReviewResponse,
)
from agent_product.services.git import GitConfirmationError, GitError, GitService
from agent_product.services.workspace import WorkspaceNotFoundError

router = APIRouter(
    prefix="/v1/workspaces/{workspace_id}/git",
    tags=["git"],
    dependencies=[Depends(require_api_key)],
)


def _git_service(request: Request, workspace_id: str, tenant_id: str) -> GitService:
    try:
        workspace = request.app.state.workspace_registry.get(workspace_id, tenant_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    assert workspace is not None
    try:
        return GitService(workspace)
    except GitError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc


def _git_error(exc: GitError) -> HTTPException:
    code = (
        status.HTTP_409_CONFLICT
        if isinstance(exc, GitConfirmationError)
        else status.HTTP_422_UNPROCESSABLE_ENTITY
    )
    return HTTPException(status_code=code, detail=str(exc))


@router.get("/review", response_model=GitReviewResponse)
async def review_changes(
    workspace_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> GitReviewResponse:
    service = _git_service(request, workspace_id, tenant_id)
    try:
        result = await asyncio.to_thread(service.review)
    except GitError as exc:
        raise _git_error(exc) from exc
    return GitReviewResponse.model_validate(result)


@router.post("/commit/prepare", response_model=GitConfirmationResponse)
async def prepare_commit(
    workspace_id: str,
    body: GitCommitPrepareRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> GitConfirmationResponse:
    service = _git_service(request, workspace_id, tenant_id)
    try:
        intent, details = await asyncio.to_thread(
            service.prepare_commit,
            request.app.state.git_intents,
            tenant_id=tenant_id,
            message=body.message,
        )
    except GitError as exc:
        raise _git_error(exc) from exc
    return GitConfirmationResponse(
        confirmation_id=intent.id,
        action="commit",
        title="Create Git commit?",
        details=details,
        expires_at=intent.expires_at.isoformat(),
    )


@router.post("/commit", response_model=GitCommitResponse)
async def confirm_commit(
    workspace_id: str,
    body: GitConfirmRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> GitCommitResponse:
    service = _git_service(request, workspace_id, tenant_id)
    try:
        intent = request.app.state.git_intents.consume(
            body.confirmation_id,
            action="commit",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        result = await asyncio.to_thread(service.commit, intent)
    except GitError as exc:
        raise _git_error(exc) from exc
    return GitCommitResponse.model_validate(result)


@router.post("/push/prepare", response_model=GitConfirmationResponse)
async def prepare_push(
    workspace_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> GitConfirmationResponse:
    service = _git_service(request, workspace_id, tenant_id)
    try:
        intent, details = await asyncio.to_thread(
            service.prepare_push,
            request.app.state.git_intents,
            tenant_id=tenant_id,
        )
    except GitError as exc:
        raise _git_error(exc) from exc
    return GitConfirmationResponse(
        confirmation_id=intent.id,
        action="push",
        title="Push this branch?",
        details=details,
        expires_at=intent.expires_at.isoformat(),
    )


@router.post("/push", response_model=GitPushResponse)
async def confirm_push(
    workspace_id: str,
    body: GitConfirmRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> GitPushResponse:
    service = _git_service(request, workspace_id, tenant_id)
    try:
        intent = request.app.state.git_intents.consume(
            body.confirmation_id,
            action="push",
            tenant_id=tenant_id,
            workspace_id=workspace_id,
        )
        result = await asyncio.to_thread(service.push, intent)
    except GitError as exc:
        raise _git_error(exc) from exc
    return GitPushResponse.model_validate(result)
