from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.schemas.knowledge import (
    KnowledgeDocumentResponse,
    KnowledgeDocumentSummary,
    KnowledgeDocumentWriteRequest,
    KnowledgeManagedDocument,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
)
from agent_product.services.knowledge import (
    KnowledgeConflictError,
    KnowledgeDocumentNotFoundError,
    KnowledgeError,
)

router = APIRouter(
    prefix="/v1/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/documents", response_model=list[KnowledgeDocumentSummary])
async def list_knowledge_documents(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> list[KnowledgeDocumentSummary]:
    del tenant_id
    return [
        KnowledgeDocumentSummary.model_validate(item)
        for item in request.app.state.knowledge_base.list_documents()
    ]


@router.get("/manage/documents", response_model=list[KnowledgeDocumentSummary])
async def list_managed_knowledge_documents(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> list[KnowledgeDocumentSummary]:
    del tenant_id
    return [
        KnowledgeDocumentSummary.model_validate(item)
        for item in request.app.state.knowledge_base.list_documents(include_inactive=True)
    ]


@router.get(
    "/manage/documents/{document_id:path}",
    response_model=KnowledgeManagedDocument,
)
async def get_managed_knowledge_document(
    document_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeManagedDocument:
    del tenant_id
    try:
        result = request.app.state.knowledge_base.get_document(
            document_id,
            include_inactive=True,
        )
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return KnowledgeManagedDocument.model_validate(result)


@router.post(
    "/manage/documents",
    response_model=KnowledgeManagedDocument,
    status_code=status.HTTP_201_CREATED,
)
async def create_managed_knowledge_document(
    body: KnowledgeDocumentWriteRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeManagedDocument:
    del tenant_id
    if body.expected_revision is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Do not provide expected_revision when creating a document",
        )
    try:
        result = request.app.state.knowledge_base.save_document(**body.model_dump())
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return KnowledgeManagedDocument.model_validate(result)


@router.put(
    "/manage/documents/{document_id:path}",
    response_model=KnowledgeManagedDocument,
)
async def update_managed_knowledge_document(
    document_id: str,
    body: KnowledgeDocumentWriteRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeManagedDocument:
    del tenant_id
    if document_id != body.document_id:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The document ID cannot be changed after creation",
        )
    try:
        result = request.app.state.knowledge_base.save_document(**body.model_dump())
    except KnowledgeConflictError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return KnowledgeManagedDocument.model_validate(result)


@router.post("/search", response_model=KnowledgeSearchResponse)
async def search_knowledge(
    body: KnowledgeSearchRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeSearchResponse:
    del tenant_id
    try:
        result = request.app.state.knowledge_base.search(
            body.query,
            limit=body.limit,
            tags=body.tags,
        )
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return KnowledgeSearchResponse.model_validate(result)


@router.get("/documents/{document_id:path}", response_model=KnowledgeDocumentResponse)
async def read_knowledge_document(
    document_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    start_line: int = Query(default=1, ge=1),
    end_line: int = Query(default=240, ge=1),
) -> KnowledgeDocumentResponse:
    del tenant_id
    try:
        result = request.app.state.knowledge_base.read_document(
            document_id,
            start_line=start_line,
            end_line=end_line,
        )
    except KnowledgeDocumentNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return KnowledgeDocumentResponse.model_validate(result)
