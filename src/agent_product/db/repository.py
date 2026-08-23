from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agent_product.db.models import Conversation, ConversationProfile


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

    async def create(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        profile_id: str,
        profile_version: str,
        profile_hash: str,
    ) -> Conversation:
        conversation = Conversation(id=conversation_id, tenant_id=tenant_id)
        binding = ConversationProfile(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
        )
        self.session.add_all((conversation, binding))
        await self.session.commit()
        await self.session.refresh(conversation)
        return conversation

    async def get_profile(
        self,
        conversation_id: str,
        tenant_id: str,
    ) -> ConversationProfile | None:
        statement = select(ConversationProfile).where(
            ConversationProfile.conversation_id == conversation_id,
            ConversationProfile.tenant_id == tenant_id,
        )
        return await self.session.scalar(statement)

    async def bind_profile(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        profile_id: str,
        profile_version: str,
        profile_hash: str,
    ) -> ConversationProfile:
        existing = await self.get_profile(conversation_id, tenant_id)
        if existing is not None:
            return existing
        binding = ConversationProfile(
            conversation_id=conversation_id,
            tenant_id=tenant_id,
            profile_id=profile_id,
            profile_version=profile_version,
            profile_hash=profile_hash,
        )
        self.session.add(binding)
        try:
            await self.session.commit()
        except IntegrityError:
            await self.session.rollback()
            concurrent = await self.get_profile(conversation_id, tenant_id)
            if concurrent is None:
                raise
            return concurrent
        await self.session.refresh(binding)
        return binding

    async def update_profile_hash(
        self,
        conversation_id: str,
        tenant_id: str,
        *,
        expected_hash: str,
        profile_hash: str,
    ) -> bool:
        statement = (
            update(ConversationProfile)
            .where(
                ConversationProfile.conversation_id == conversation_id,
                ConversationProfile.tenant_id == tenant_id,
                ConversationProfile.profile_hash == expected_hash,
            )
            .values(profile_hash=profile_hash)
        )
        result = await self.session.execute(statement)
        await self.session.commit()
        return result.rowcount == 1

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
