from pydantic import BaseModel


class AgentModelResponse(BaseModel):
    provider: str
    model: str
    is_active: bool = True
