import logging
import time
from collections import Counter, defaultdict, deque
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse, Response
from pydantic_ai import ModelMessage, ModelMessagesTypeAdapter
from pydantic_ai.messages import ModelResponse, RetryPromptPart, ToolCallPart, ToolReturnPart
from pydantic_ai.ui.vercel_ai import VercelAIAdapter
from pydantic_core import to_jsonable_python
from sqlalchemy.ext.asyncio import AsyncSession

from agent_product.core.security import require_api_key, require_customer_id, require_tenant_id
from agent_product.db.events import ConversationEventRepository
from agent_product.db.repository import ConversationConflictError, ConversationRepository
from agent_product.db.session import get_session
from agent_product.schemas.chat import ChatRequest, ChatResponse, ConversationResponse
from agent_product.services.agent import AgentDependencies, run_agent
from agent_product.services.agent_profiles import AgentProfileError
from agent_product.services.agent_runtime import AgentRegistration, AgentRuntime
from agent_product.services.conversation import load_history
from agent_product.services.conversation_events import completed_run_events, request_snapshot
from agent_product.services.workspace import WorkspaceNotFoundError

logger = logging.getLogger(__name__)

APPROVAL_REQUIRED_WORKSPACE_TOOLS = frozenset(
    {"write_file", "create_file", "apply_patch"}
)
APPROVAL_REQUIRED_TOOLS = APPROVAL_REQUIRED_WORKSPACE_TOOLS | {"create_support_case"}
WORKSPACE_BINDING_METADATA_KEY = "agent_product_workspace_id"


@dataclass(frozen=True, slots=True)
class PendingToolCall:
    tool_name: str
    workspace_id: str | None
    has_workspace_binding: bool


def _agent_runtime(request: Request) -> AgentRuntime:
    return request.app.state.agent_runtime


def _requested_profile_id(request: Request, body_profile_id: str | None = None) -> str | None:
    header_profile_id = request.headers.get("X-Agent-Profile")
    if body_profile_id and header_profile_id and body_profile_id != header_profile_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The body and X-Agent-Profile header select different Agent Profiles",
        )
    return body_profile_id or header_profile_id


def _resolve_registration(
    request: Request,
    profile_id: str | None,
) -> AgentRegistration:
    try:
        return _agent_runtime(request).get(profile_id)
    except AgentProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


async def _registration_for_conversation(
    request: Request,
    repository: ConversationRepository,
    conversation_id: str,
    tenant_id: str,
    requested_profile_id: str | None,
) -> AgentRegistration:
    binding = await repository.get_profile(conversation_id, tenant_id)
    if binding is None:
        registration = _resolve_registration(request, requested_profile_id)
        binding = await repository.bind_profile(
            conversation_id,
            tenant_id,
            profile_id=registration.profile.id,
            profile_version=registration.profile.version,
            profile_hash=registration.composition_hash,
        )
        if binding.profile_id != registration.profile.id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"Conversation was concurrently bound to Agent Profile "
                    f"{binding.profile_id!r}; retry using that Profile"
                ),
            )

    if requested_profile_id and requested_profile_id != binding.profile_id:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Conversation is bound to Agent Profile {binding.profile_id!r}; "
                "start a new conversation to use another Profile"
            ),
        )
    try:
        registration = _agent_runtime(request).get_bound(
            binding.profile_id,
            binding.profile_version,
        )
    except AgentProfileError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    if binding.profile_hash == registration.profile.manifest_hash:
        # Compatibility with the first Profile binding format, which stored
        # only the declarative manifest before effective composition hashing.
        upgraded = await repository.update_profile_hash(
            conversation_id,
            tenant_id,
            expected_hash=binding.profile_hash,
            profile_hash=registration.composition_hash,
        )
        if upgraded:
            binding.profile_hash = registration.composition_hash
    if binding.profile_hash != registration.composition_hash:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Agent Profile {binding.profile_id!r} changed without a version bump; "
                "restore its manifest or create a new Profile version"
            ),
        )
    return registration


def _dependencies(
    request: Request,
    tenant_id: str,
    customer_id: str,
    registration: AgentRegistration,
) -> AgentDependencies:
    workspace = (
        _get_workspace(request, tenant_id)
        if "workspace-read" in registration.active_capability_packs
        else None
    )
    return AgentDependencies(
        tenant_id=tenant_id,
        request_id=request.state.request_id,
        customer_id=customer_id,
        workspace=workspace,
        knowledge_base=_agent_runtime(request).scope_knowledge(
            registration,
            request.app.state.knowledge_base,
        ),
        skills=(
            request.app.state.skill_registry.scoped(registration.profile.id)
            if "skills" in registration.active_capability_packs
            else None
        ),
        support_service=(
            request.app.state.support_service
            if "support" in registration.active_capability_packs
            else None
        ),
        profile_id=registration.profile.id,
    )


async def _record_conversation_created(
    session: AsyncSession,
    conversation_id: str,
    tenant_id: str,
    registration: AgentRegistration,
) -> None:
    await ConversationEventRepository(session).append(
        conversation_id,
        tenant_id,
        "conversation.created",
        {
            "profile_id": registration.profile.id,
            "profile_version": registration.profile.version,
            "composition_hash": registration.composition_hash,
        },
    )


async def _record_turn_started(
    session: AsyncSession,
    conversation_id: str,
    tenant_id: str,
    turn_id: str,
    registration: AgentRegistration,
    request: Request,
) -> None:
    snapshot = request_snapshot(turn_id, registration, request.app.state.settings)
    snapshot["workspace_id"] = (
        request.headers.get("X-Workspace-ID")
        if "workspace-read" in registration.active_capability_packs
        else None
    )
    snapshot["skill_catalog"] = (
        [
            summary.as_dict()
            for summary in request.app.state.skill_registry.scoped(
                registration.profile.id
            ).list()
        ]
        if "skills" in registration.active_capability_packs
        else []
    )
    await ConversationEventRepository(session).append_many(
        conversation_id,
        tenant_id,
        (
            ("turn.started", {"turn_id": turn_id}),
            (
                "request.prepared",
                snapshot,
            ),
        ),
    )


async def _record_turn_failed(
    session: AsyncSession,
    conversation_id: str,
    tenant_id: str,
    turn_id: str,
    error: Exception,
    *,
    duration_ms: int | None = None,
) -> None:
    try:
        await ConversationEventRepository(session).append(
            conversation_id,
            tenant_id,
            "turn.failed",
            {
                "turn_id": turn_id,
                "error_type": type(error).__name__,
                "message": str(error)[:2_000],
                "duration_ms": duration_ms,
            },
        )
    except Exception:
        logger.exception(
            "Failed to append turn failure event",
            extra={"conversation_id": conversation_id, "turn_id": turn_id},
        )

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


def _pending_tool_calls(history: Sequence[ModelMessage]) -> dict[str, PendingToolCall]:
    calls: dict[str, PendingToolCall] = {}
    for message in history:
        if isinstance(message, ModelResponse):
            metadata = message.metadata or {}
            has_workspace_binding = WORKSPACE_BINDING_METADATA_KEY in metadata
            raw_workspace_id = metadata.get(WORKSPACE_BINDING_METADATA_KEY)
            workspace_id = raw_workspace_id if isinstance(raw_workspace_id, str) else None
            for part in message.parts:
                if isinstance(part, ToolCallPart):
                    calls[part.tool_call_id] = PendingToolCall(
                        tool_name=part.tool_name,
                        workspace_id=workspace_id,
                        has_workspace_binding=has_workspace_binding,
                    )
        else:
            for part in message.parts:
                if isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id:
                    calls.pop(part.tool_call_id, None)
    return calls


def _history_json_with_workspace_binding(result, workspace_id: str | None) -> str:
    changed = False
    for message in result.new_messages():
        if not isinstance(message, ModelResponse) or not any(
            isinstance(part, ToolCallPart)
            and part.tool_name in APPROVAL_REQUIRED_WORKSPACE_TOOLS
            for part in message.parts
        ):
            continue
        metadata = dict(message.metadata or {})
        metadata[WORKSPACE_BINDING_METADATA_KEY] = workspace_id
        message.metadata = metadata
        changed = True

    if not changed:
        return result.all_messages_json().decode("utf-8")
    return ModelMessagesTypeAdapter.dump_json(result.all_messages()).decode("utf-8")


def _history_with_unique_ui_tool_call_ids(
    history: Sequence[ModelMessage],
) -> list[ModelMessage]:
    """Disambiguate provider-reused IDs in the UI without changing trusted history."""
    totals = Counter(
        part.tool_call_id
        for message in history
        if isinstance(message, ModelResponse)
        for part in message.parts
        if isinstance(part, ToolCallPart)
    )
    seen: Counter[str] = Counter()
    unresolved: dict[str, deque[str]] = defaultdict(deque)
    normalized: list[ModelMessage] = []

    for message in history:
        changed = False
        normalized_parts = []
        if isinstance(message, ModelResponse):
            for part in message.parts:
                if not isinstance(part, ToolCallPart):
                    normalized_parts.append(part)
                    continue
                original_id = part.tool_call_id
                seen[original_id] += 1
                normalized_id = original_id
                if seen[original_id] < totals[original_id]:
                    normalized_id = str(
                        uuid5(
                            NAMESPACE_URL,
                            f"agent-product-history:{original_id}:{seen[original_id]}",
                        )
                    )
                unresolved[original_id].append(normalized_id)
                normalized_parts.append(
                    part
                    if normalized_id == original_id
                    else replace(part, tool_call_id=normalized_id)
                )
                changed |= normalized_id != original_id
        else:
            for part in message.parts:
                if not isinstance(part, (ToolReturnPart, RetryPromptPart)):
                    normalized_parts.append(part)
                    continue
                queue = unresolved.get(part.tool_call_id)
                normalized_id = queue.popleft() if queue else part.tool_call_id
                normalized_parts.append(
                    part
                    if normalized_id == part.tool_call_id
                    else replace(part, tool_call_id=normalized_id)
                )
                changed |= normalized_id != part.tool_call_id
        normalized.append(
            replace(message, parts=normalized_parts) if changed else message
        )

    return normalized


@router.post("/chat/stream", response_class=Response)
async def chat_stream(
    request: Request,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    customer_id: Annotated[str, Depends(require_customer_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
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

    requested_profile_id = _requested_profile_id(request)
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
        registration = _resolve_registration(request, requested_profile_id)
        conversation = await repository.create(
            str(conversation_id),
            tenant_id,
            profile_id=registration.profile.id,
            profile_version=registration.profile.version,
            profile_hash=registration.composition_hash,
        )
        await _record_conversation_created(
            session,
            conversation.id,
            tenant_id,
            registration,
        )
    else:
        registration = await _registration_for_conversation(
            request,
            repository,
            conversation.id,
            tenant_id,
            requested_profile_id,
        )
    agent = registration.agent

    expected_version = conversation.version
    history = load_history(conversation)
    pending_calls = _pending_tool_calls(history)

    if submitted_role == "user" and pending_calls:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Approve or reject the pending workspace change before sending another message",
        )

    dependencies = _dependencies(request, tenant_id, customer_id, registration)
    workspace = dependencies.workspace
    workspace_id = workspace.id if workspace is not None else None
    turn_id = str(uuid4())
    turn_started_at = time.perf_counter()
    await _record_turn_started(
        session,
        conversation.id,
        tenant_id,
        turn_id,
        registration,
        request,
    )

    async def persist_completed_run(result) -> None:
        try:
            await repository.save_history(
                conversation_id=conversation.id,
                tenant_id=tenant_id,
                expected_version=expected_version,
                history_json=_history_json_with_workspace_binding(result, workspace_id),
            )
            await ConversationEventRepository(session).append_many(
                conversation.id,
                tenant_id,
                completed_run_events(
                    turn_id,
                    result,
                    duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
                ),
            )
        except ConversationConflictError as exc:
            logger.exception(
                "Streaming conversation changed before completion",
                extra={"conversation_id": conversation.id},
            )
            await _record_turn_failed(
                session,
                conversation.id,
                tenant_id,
                turn_id,
                exc,
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
            )
            raise
        except Exception as exc:
            await _record_turn_failed(
                session,
                conversation.id,
                tenant_id,
                turn_id,
                exc,
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
            )
            raise

    if submitted_role == "user":
        try:
            return await VercelAIAdapter.dispatch_request(
                request,
                agent=agent,
                sdk_version=7,
                message_history=history,
                conversation_id=conversation.id,
                deps=dependencies,
                on_complete=persist_completed_run,
            )
        except Exception as exc:
            await _record_turn_failed(
                session,
                conversation.id,
                tenant_id,
                turn_id,
                exc,
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
            )
            raise

    client_adapter = VercelAIAdapter(
        agent=agent,
        run_input=run_input,
        accept=request.headers.get("accept"),
        sdk_version=7,
    )
    deferred_results = client_adapter.deferred_tool_results
    approval_ids = set(deferred_results.approvals) if deferred_results is not None else set()
    if (
        deferred_results is None
        or deferred_results.calls
        or not approval_ids
        or approval_ids != set(pending_calls)
        or any(
            pending_calls[tool_call_id].tool_name not in APPROVAL_REQUIRED_TOOLS
            for tool_call_id in approval_ids
        )
        or any(
            deferred_results.approvals[tool_call_id] is True
            and pending_calls[tool_call_id].tool_name in APPROVAL_REQUIRED_WORKSPACE_TOOLS
            and (
                not pending_calls[tool_call_id].has_workspace_binding
                or pending_calls[tool_call_id].workspace_id != workspace_id
            )
            for tool_call_id in approval_ids
        )
    ):
        await ConversationEventRepository(session).append(
            conversation.id,
            tenant_id,
            "turn.rejected",
            {"turn_id": turn_id, "reason": "approval-response-mismatch"},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="The approval response does not match the pending workspace change",
        )

    await ConversationEventRepository(session).append(
        conversation.id,
        tenant_id,
        "approval.decided",
        {
            "turn_id": turn_id,
            "decisions": [
                {
                    "tool_call_id": tool_call_id,
                    "approved": deferred_results.approvals[tool_call_id] is True,
                }
                for tool_call_id in sorted(approval_ids)
            ],
        },
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
    customer_id: Annotated[str, Depends(require_customer_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ChatResponse:
    repository = ConversationRepository(session)
    requested_profile_id = _requested_profile_id(request, body.profile_id)
    if body.conversation_id is None:
        registration = _resolve_registration(request, requested_profile_id)
        conversation = await repository.create(
            str(uuid4()),
            tenant_id,
            profile_id=registration.profile.id,
            profile_version=registration.profile.version,
            profile_hash=registration.composition_hash,
        )
        await _record_conversation_created(
            session,
            conversation.id,
            tenant_id,
            registration,
        )
    else:
        conversation = await repository.get(str(body.conversation_id), tenant_id)
        if conversation is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        registration = await _registration_for_conversation(
            request,
            repository,
            conversation.id,
            tenant_id,
            requested_profile_id,
        )
    agent = registration.agent

    request_id = request.state.request_id
    dependencies = _dependencies(request, tenant_id, customer_id, registration)
    turn_id = str(uuid4())
    turn_started_at = time.perf_counter()
    await _record_turn_started(
        session,
        conversation.id,
        tenant_id,
        turn_id,
        registration,
        request,
    )
    try:
        reply = await run_agent(
            agent=agent,
            prompt=body.message,
            history=load_history(conversation),
            conversation_id=conversation.id,
            dependencies=dependencies,
        )
        version = await repository.save_history(
            conversation_id=conversation.id,
            tenant_id=tenant_id,
            expected_version=conversation.version,
            history_json=reply.history_json,
        )
        await ConversationEventRepository(session).append_many(
            conversation.id,
            tenant_id,
            completed_run_events(
                turn_id,
                reply.run_result,
                duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
            ),
        )
    except ConversationConflictError as exc:
        await _record_turn_failed(
            session,
            conversation.id,
            tenant_id,
            turn_id,
            exc,
            duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Conversation changed during this request; retry with the latest state",
        ) from exc
    except HTTPException as exc:
        await _record_turn_failed(
            session,
            conversation.id,
            tenant_id,
            turn_id,
            exc,
            duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
        )
        raise
    except Exception as exc:
        logger.exception("Agent execution failed", extra={"request_id": request_id})
        await _record_turn_failed(
            session,
            conversation.id,
            tenant_id,
            turn_id,
            exc,
            duration_ms=round((time.perf_counter() - turn_started_at) * 1000),
        )
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail="Agent execution failed",
        ) from exc

    return ChatResponse(
        conversation_id=UUID(conversation.id),
        version=version,
        message=reply.output,
        model=request.app.state.settings.ai_model,
        profile_id=registration.profile.id,
        profile_version=registration.profile.version,
        usage=reply.usage,
    )


@router.get("/conversations/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    request: Request,
    conversation_id: UUID,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> ConversationResponse:
    repository = ConversationRepository(session)
    conversation = await repository.get(str(conversation_id), tenant_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    registration = await _registration_for_conversation(
        request,
        repository,
        conversation.id,
        tenant_id,
        None,
    )
    return ConversationResponse(
        id=UUID(conversation.id),
        tenant_id=conversation.tenant_id,
        version=conversation.version,
        profile_id=registration.profile.id,
        profile_version=registration.profile.version,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


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
    history = _history_with_unique_ui_tool_call_ids(load_history(conversation))
    ui_messages = VercelAIAdapter.dump_messages(history, sdk_version=7)
    return JSONResponse(content=to_jsonable_python(ui_messages, by_alias=True))
