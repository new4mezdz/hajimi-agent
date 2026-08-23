from __future__ import annotations

import json
from collections.abc import Iterable
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agent_product.db.models import ConversationEvent

MAX_EVENT_PAYLOAD_BYTES = 4_000_000


class ConversationEventError(ValueError):
    pass


class ConversationEventRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def _encode(payload: dict[str, Any]) -> str:
        try:
            encoded = json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
        except (TypeError, ValueError) as exc:
            raise ConversationEventError("Conversation event payload must be JSON") from exc
        if len(encoded.encode("utf-8")) > MAX_EVENT_PAYLOAD_BYTES:
            raise ConversationEventError("Conversation event payload exceeds 4 MB")
        return encoded

    async def append_many(
        self,
        conversation_id: str,
        tenant_id: str,
        events: Iterable[tuple[str, dict[str, Any]]],
    ) -> list[ConversationEvent]:
        rows: list[ConversationEvent] = []
        for event_type, payload in events:
            if not event_type or len(event_type) > 100:
                raise ConversationEventError("Conversation event type is invalid")
            rows.append(
                ConversationEvent(
                    conversation_id=conversation_id,
                    tenant_id=tenant_id,
                    event_type=event_type,
                    payload_json=self._encode(payload),
                )
            )
        if not rows:
            return []
        self.session.add_all(rows)
        await self.session.commit()
        return rows

    async def append(
        self,
        conversation_id: str,
        tenant_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> ConversationEvent:
        return (
            await self.append_many(
                conversation_id,
                tenant_id,
                ((event_type, payload),),
            )
        )[0]

    async def list(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        after_id: int = 0,
        limit: int = 200,
    ) -> list[ConversationEvent]:
        statement = (
            select(ConversationEvent)
            .where(
                ConversationEvent.conversation_id == conversation_id,
                ConversationEvent.tenant_id == tenant_id,
                ConversationEvent.id > after_id,
            )
            .order_by(ConversationEvent.id)
            .limit(limit)
        )
        return list(await self.session.scalars(statement))
