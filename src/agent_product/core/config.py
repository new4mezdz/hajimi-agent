from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-backed application settings."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_name: str = "哈基米sama"
    app_env: str = "development"
    log_level: str = "INFO"

    ai_model: str = "openai:gpt-4.1-mini"
    agent_instructions: str = (
        "You are 哈基米sama, a reliable product assistant represented by a friendly orange cat. "
        "Be concise, accurate, and use tools when useful."
    )
    deepseek_api_key: SecretStr | None = None
    web_search_enabled: bool = True
    web_search_max_uses: int = 3
    deepseek_anthropic_base_url: str = "https://api.deepseek.com/anthropic"

    database_url: str = "sqlite+aiosqlite:///./data/agent.db"
    auto_create_tables: bool = True
    sql_echo: bool = False

    service_api_key: str | None = None
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://tauri.localhost,https://tauri.localhost,tauri://localhost"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    def prepare_local_directories(self) -> None:
        if self.database_url.startswith("sqlite"):
            Path("data").mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
