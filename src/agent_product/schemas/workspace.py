from pydantic import BaseModel, Field


class WorkspaceCreateRequest(BaseModel):
    path: str = Field(min_length=1, max_length=4096)


class WorkspaceResponse(BaseModel):
    id: str
    name: str
    path: str
