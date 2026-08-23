from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.schemas.knowledge import (
    KnowledgeChunkPolicyResponse,
    KnowledgeContextResponse,
    KnowledgeDocumentResponse,
    KnowledgeDocumentSummary,
    KnowledgeDocumentWriteRequest,
    KnowledgeIndexStatusResponse,
    KnowledgeLibrarySummary,
    KnowledgeManagedDocument,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSourceSummary,
)
from agent_product.services.knowledge import (
    KnowledgeChunkNotFoundError,
    KnowledgeConflictError,
    KnowledgeDocumentNotFoundError,
    KnowledgeError,
)

router = APIRouter(
    prefix="/v1/knowledge",
    tags=["knowledge"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/libraries", response_model=list[KnowledgeLibrarySummary])
async def list_knowledge_libraries(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> list[KnowledgeLibrarySummary]:
    del tenant_id
    return [
        KnowledgeLibrarySummary.model_validate(library)
        for library in request.app.state.knowledge_base.list_libraries()
    ]


@router.get(
    "/libraries/{library_id}/sources",
    response_model=list[KnowledgeSourceSummary],
)
async def list_knowledge_sources(
    library_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> list[KnowledgeSourceSummary]:
    del tenant_id
    try:
        sources = request.app.state.knowledge_base.list_sources(library_id)
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    return [KnowledgeSourceSummary.model_validate(source) for source in sources]


@router.get("/chunk-policy", response_model=KnowledgeChunkPolicyResponse)
async def get_knowledge_chunk_policy(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeChunkPolicyResponse:
    del tenant_id
    return KnowledgeChunkPolicyResponse.model_validate(
        request.app.state.knowledge_base.chunk_policy()
    )


@router.get("/index-status", response_model=KnowledgeIndexStatusResponse)
async def get_knowledge_index_status(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeIndexStatusResponse:
    del tenant_id
    return KnowledgeIndexStatusResponse.model_validate(
        request.app.state.knowledge_base.index_status()
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
            library_ids=body.library_ids,
            include_context=body.include_context,
        )
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return KnowledgeSearchResponse.model_validate(result)


@router.get(
    "/chunks/{chunk_id}/context",
    response_model=KnowledgeContextResponse,
)
async def read_knowledge_context(
    chunk_id: str,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
) -> KnowledgeContextResponse:
    del tenant_id
    try:
        result = request.app.state.knowledge_base.read_context(chunk_id)
    except KnowledgeChunkNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except KnowledgeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc
    return KnowledgeContextResponse.model_validate(result)


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
