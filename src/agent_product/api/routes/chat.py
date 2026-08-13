import logging
from collections.abc import Sequence
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic_ai import Agent, ModelMessage
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
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
from agent_product.services.workspace import WorkspaceNotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/v1",
    tags=["agent"],
    dependencies=[Depends(require_api_key)],
)


def _get_workspace(request: Request, tenant_id: str):
    workspace_id = request.headers.get("X-Workspace-ID")
    try:
        return request.app.state.workspace_registry.get(workspace_id, tenant_id)
    except WorkspaceNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


def _pending_tool_calls(history: Sequence[ModelMessage]) -> dict[str, str]:
    calls: dict[str, str] = {}
    resolved: set[str] = set()
    for message in history:
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    calls[part.tool_call_id] = part.tool_name
        else:
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    resolved.add(part.tool_call_id)
    return {
        tool_call_id: tool_name
        for tool_call_id, tool_name in calls.items()
        if tool_call_id not in resolved
    }


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
            detail="Send exactly one new message per request",
        )
    submitted_role = run_input.messages[0].role
    if submitted_role not in {"user", "assistant"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The submitted message must be a user message or an approval response",
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
        if submitted_role == "assistant":
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="There is no pending tool call to approve",
            )
        conversation = await repository.create(str(conversation_id), tenant_id)

    expected_version = conversation.version
    history = load_history(conversation)

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

    dependencies = AgentDependencies(
        tenant_id=tenant_id,
        request_id=request.state.request_id,
        workspace=_get_workspace(request, tenant_id),
        knowledge_base=request.app.state.knowledge_base,
    )
    if submitted_role == "user":
        return await VercelAIAdapter.dispatch_request(
            request,
            agent=agent,
            sdk_version=7,
            message_history=history,
            conversation_id=conversation.id,
            deps=dependencies,
            on_complete=persist_completed_run,
        )

    client_adapter = VercelAIAdapter(
        agent=agent,
        run_input=run_input,
        accept=request.headers.get("accept"),
        sdk_version=7,
    )
    deferred_results = client_adapter.deferred_tool_results
    pending_calls = _pending_tool_calls(history)
    approval_ids = set(deferred_results.approvals) if deferred_results is not None else set()
    if (
        deferred_results is None
        or deferred_results.calls
        or not approval_ids
        or approval_ids != set(pending_calls)
        or any(pending_calls[tool_call_id] != "write_file" for tool_call_id in approval_ids)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The approval response does not match the pending file write",
        )

    # Approval booleans come from the client, but the tool name and arguments must come only
    # from trusted server-side history. Dropping the submitted assistant message prevents a
    # browser from changing the path or content while approving a real tool-call ID.
    safe_run_input = run_input.model_copy(update={"messages": []})
    safe_adapter = VercelAIAdapter(
        agent=agent,
        run_input=safe_run_input,
        accept=request.headers.get("accept"),
        sdk_version=7,
    )
    return safe_adapter.streaming_response(
        safe_adapter.run_stream(
            message_history=history,
            deferred_tool_results=deferred_results,
            conversation_id=conversation.id,
            deps=dependencies,
            on_complete=persist_completed_run,
        )
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
            dependencies=AgentDependencies(
                tenant_id=tenant_id,
                request_id=request_id,
                workspace=_get_workspace(request, tenant_id),
                knowledge_base=request.app.state.knowledge_base,
            ),
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
