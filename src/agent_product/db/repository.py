from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agent_product.db.models import Conversation


class ConversationConflictError(RuntimeError):
    """Raised when another request has already updated a conversation."""


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, conversation_id: str, tenant_id: str) -> Conversation | None:
        statement = select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.tenant_id == tenant_id,
        )
        return await self.session.scalar(statement)

    async def get_by_id(self, conversation_id: str) -> Conversation | None:
        statement = select(Conversation).where(Conversation.id == conversation_id)
        return await self.session.scalar(statement)

    async def create(self, conversation_id: str, tenant_id: str) -> Conversation:
        conversation = Conversation(id=conversation_id, tenant_id=tenant_id)
        self.session.add(conversation)
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def save_history(
        self,
        conversation_id: str,
        tenant_id: str,
        expected_version: int,
        history_json: str,
    ) -> int:
        new_version = expected_version + 1
        statement = (
            update(Conversation)
            .where(
                Conversation.id == conversation_id,
                Conversation.tenant_id == tenant_id,
                Conversation.version == expected_version,
            )
            .values(
                history_json=history_json,
                version=new_version,
                updated_at=datetime.now(UTC),
            )
        )
        result = await self.session.execute(statement)
        if result.rowcount != 1:
            await self.session.rollback()
            raise ConversationConflictError(conversation_id)
        await self.session.commit()
        return new_version
