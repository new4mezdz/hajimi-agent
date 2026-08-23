from datetime import datetime
from typing import Any

from pydantic import BaseModel


class ConversationEventResponse(BaseModel):
    id: int
    event_type: str
    event_version: int
    payload: dict[str, Any]
    created_at: datetime
