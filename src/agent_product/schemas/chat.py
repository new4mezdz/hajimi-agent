from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=20_000)
    conversation_id: UUID | None = None
    profile_id: str | None = Field(default=None, min_length=1, max_length=64)


class TokenUsage(BaseModel):
    requests: int = 0
    input_tokens: int = 0
    output_tokens: int = 0


class ChatResponse(BaseModel):
    conversation_id: UUID
    version: int
    message: str
    model: str
    profile_id: str
    profile_version: str
    usage: TokenUsage


class ConversationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    tenant_id: str
    version: int
    profile_id: str
    profile_version: str
    created_at: datetime
    updated_at: datetime
