from pydantic import BaseModel


class SkillSummaryResponse(BaseModel):
    name: str
    description: str
    version: str
    status: str
    profiles: list[str]
    tags: list[str]
    model_invocable: bool
    user_invocable: bool
    source: str
    revision: str


class SkillDefinitionResponse(SkillSummaryResponse):
    content: str
    resource_base: str | None = None
