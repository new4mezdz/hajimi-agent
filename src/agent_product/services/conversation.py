from pydantic_ai import ModelMessagesTypeAdapter

from agent_product.db.models import Conversation


def load_history(conversation: Conversation):
    return ModelMessagesTypeAdapter.validate_json(conversation.history_json)
