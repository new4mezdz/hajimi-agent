import unicodedata
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


def _to_camel(value: str) -> str:
    head, *tail = value.split("_")
    return head + "".join(part.title() for part in tail)


class AgentSettingsInput(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    provider: Literal["openai", "deepseek", "anthropic"]
    model: str = Field(min_length=3, max_length=200)
    web_search_enabled: bool
    workspace_write_enabled: bool
    agent_instructions: str = Field(min_length=1, max_length=20_000)
    api_key: str | None = Field(default=None, max_length=8192)
    clear_api_key: bool = False

    @model_validator(mode="after")
    def validate_model_provider(self) -> "AgentSettingsInput":
        if not self.model.startswith(f"{self.provider}:") or any(
            unicodedata.category(character).startswith("C") for character in self.model
        ):
            raise ValueError(f"The model ID must start with {self.provider}:")
        if not self.agent_instructions.strip():
            raise ValueError("Agent instructions cannot be empty")
        return self


class AgentSettingsResponse(BaseModel):
    model_config = ConfigDict(alias_generator=_to_camel, populate_by_name=True)

    provider: Literal["openai", "deepseek", "anthropic"]
    model: str
    configured_models: dict[str, str]
    web_search_enabled: bool
    workspace_write_enabled: bool
    agent_instructions: str
    api_key_configured: bool
    api_key_previews: dict[str, str]
    configured_providers: list[str]
    secure_storage: bool
