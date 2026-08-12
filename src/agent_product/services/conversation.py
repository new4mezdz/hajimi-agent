from uuid import UUID, uuid4

from pydantic_ai import ModelMessagesTypeAdapter

from agent_product.db.models import Conversation
from agent_product.db.repository import ConversationRepository


class ConversationNotFoundError(LookupError):
    pass


async def load_or_create_conversation(
    repository: ConversationRepository,
    conversation_id: UUID | None,
    tenant_id: str,
) -> Conversation:
    if conversation_id is None:
        return await repository.create(str(uuid4()), tenant_id)

    conversation = await repository.get(str(conversation_id), tenant_id)
    if conversation is None:
        raise ConversationNotFoundError(str(conversation_id))
    return conversation


def load_history(conversation: Conversation):
    return ModelMessagesTypeAdapter.validate_json(conversation.history_json)

