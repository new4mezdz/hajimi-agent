import logging
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic_ai import Agent
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession

from agent_product.api.deps import get_agent
from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.db.repository import ConversationConflictError, ConversationRepository
from agent_product.db.session import get_session
from agent_product.schemas.chat import ChatRequest, ChatResponse, ConversationResponse
from agent_product.services.agent import AgentDependencies, run_agent
from agent_product.services.conversation import (
    ConversationNotFoundError,
    load_history,
    load_or_create_conversation,
)

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["agent"],
    dependencies=[Depends(require_api_key)],
)


@router.post("/chat/stream", response_class=Response)
async def chat_stream(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[Agent, Depends(get_agent)],
) -> Response:
    """Stream one new user message using the Vercel AI SDK v7 protocol."""
    try:
        run_input = VercelAIAdapter.build_run_input(await request.body())
        conversation_id = UUID(run_input.id)
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="The chat id must be a valid UUID",
        ) from exc

    if run_input.trigger != "submit-message" or len(run_input.messages) != 1:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Send exactly one new user message per request",
        )
    if run_input.messages[0].role != "user":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The submitted message must have the user role",
        )

    repository = ConversationRepository(session)
    conversation = await repository.get(str(conversation_id), tenant_id)
    if conversation is None:
        existing = await repository.get_by_id(str(conversation_id))
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        conversation = await repository.create(str(conversation_id), tenant_id)

    expected_version = conversation.version

    async def persist_completed_run(result) -> None:
        try:
            await repository.save_history(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                expected_version=expected_version,
                history_json=result.all_messages_json().decode("utf-8"),
            )
        except ConversationConflictError:
            logger.exception(
                "Streaming conversation changed before completion",
                extra={"conversation_id": conversation.id},
            )
            raise

    return await VercelAIAdapter.dispatch_request(
        request,
        agent=agent,
        sdk_version=7,
        message_history=load_history(conversation),
        conversation_id=conversation.id,
        deps=AgentDependencies(
            tenant_id=tenant_id,
            request_id=request.state.request_id,
        ),
        on_complete=persist_completed_run,
    )


@router.post("/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    agent: Annotated[Agent, Depends(get_agent)],
) -> ChatResponse:
    repository = ConversationRepository(session)
    try:
        conversation = await load_or_create_conversation(
            repository, body.conversation_id, tenant_id
        )
    except ConversationNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        ) from exc

    request_id = request.state.request_id
    try:
        reply = await run_agent(
            agent=agent,
            prompt=body.message,
            history=load_history(conversation),
            conversation_id=conversation.id,
            dependencies=AgentDependencies(tenant_id=tenant_id, request_id=request_id),
        )
        version = await repository.save_history(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            expected_version=conversation.version,
            history_json=reply.history_json,
        )
    except ConversationConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation changed during this request; retry with the latest state",
        ) from exc
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Agent execution failed", extra={"request_id": request_id})
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent execution failed",
        ) from exc

    return ChatResponse(
        conversation_id=UUID(conversation.id),
        version=version,
        message=reply.output,
        model=request.app.state.settings.ai_model,
        usage=reply.usage,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: UUID,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    conversation = await ConversationRepository(session).get(str(conversation_id), tenant_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return ConversationResponse.model_validate(conversation)


@router.get("/conversations/{conversation_id}/messages", response_class=JSONResponse)
async def get_conversation_messages(
    conversation_id: UUID,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> JSONResponse:
    conversation = await ConversationRepository(session).get(str(conversation_id), tenant_id)
    if conversation is None:
        existing = await ConversationRepository(session).get_by_id(str(conversation_id))
        if existing is not None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        return JSONResponse(content=[])
    ui_messages = VercelAIAdapter.dump_messages(load_history(conversation), sdk_version=7)
    return JSONResponse(content=to_jsonable_python(ui_messages, by_alias=True))
