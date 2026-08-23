import json
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from agent_product.core.security import require_api_key, require_tenant_id
from agent_product.db.events import ConversationEventRepository
from agent_product.db.repository import ConversationRepository
from agent_product.db.session import get_session
from agent_product.schemas.events import ConversationEventResponse

router = APIRouter(
    prefix="/v1/conversations",
    tags=["conversation-events"],
    dependencies=[Depends(require_api_key)],
)


@router.get("/{conversation_id}/events", response_model=list[ConversationEventResponse])
async def list_conversation_events(
    conversation_id: UUID,
    tenant_id: Annotated[str, Depends(require_tenant_id)],
    session: Annotated[AsyncSession, Depends(get_session)],
    after_id: Annotated[int, Query(ge=0)] = 0,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[ConversationEventResponse]:
    if await ConversationRepository(session).get(str(conversation_id), tenant_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    rows = await ConversationEventRepository(session).list(
        str(conversation_id),
        tenant_id,
        after_id=after_id,
        limit=limit,
    )
    return [
        ConversationEventResponse(
            id=row.id,
            event_type=row.event_type,
            event_version=1,
            payload=json.loads(row.payload_json),
            created_at=row.created_at,
        )
        for row in rows
    ]
