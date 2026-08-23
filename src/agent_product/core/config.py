from functools import lru_cache
from pathlib import Path

from pydantic import SecretStr, model_validator
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
    default_agent_profile: str = "code"
    agent_profile_dir: str | None = "agent_profiles"
    agent_instructions: str = (
        "You are 哈基米sama, a reliable product assistant represented by a friendly orange cat. "
        "Be concise, accurate, and use tools when useful."
    )
    openai_api_key: SecretStr | None = None
    deepseek_api_key: SecretStr | None = None
    anthropic_api_key: SecretStr | None = None
    web_search_enabled: bool = True
    web_search_max_uses: int = 3
    deepseek_anthropic_base_url: str = "https://api.deepseek.com/anthropic"
    workspace_write_enabled: bool = True

    database_url: str = "sqlite+aiosqlite:///./data/agent.db"
    auto_create_tables: bool = True
    sql_echo: bool = False

    knowledge_enabled: bool = True
    knowledge_dir: str = "knowledge"
    knowledge_max_results: int = 8
    knowledge_index_backend: str = "auto"
    knowledge_index_path: str = "data/knowledge-index.db"

    skills_enabled: bool = True
    skills_dir: str = "skills"
    skill_max_bytes: int = 64_000
    skill_catalog_description_max_length: int = 500

    support_enabled: bool = True
    support_demo_seed_enabled: bool = True
    support_demo_tenant_id: str = "local"
    support_demo_customer_id: str = "customer-demo-a"

    service_api_key: str | None = None
    cors_origins: str = (
        "http://localhost:3000,http://127.0.0.1:3000,"
        "http://tauri.localhost,https://tauri.localhost,tauri://localhost"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @model_validator(mode="after")
    def resolve_local_backend_defaults(self) -> "Settings":
        if self.knowledge_index_backend == "auto":
            self.knowledge_index_backend = (
                "memory" if self.app_env == "test" else "sqlite_fts5"
            )
        return self

    def prepare_local_directories(self) -> None:
        if self.database_url.startswith("sqlite"):
            Path("data").mkdir(parents=True, exist_ok=True)
        if self.knowledge_enabled:
            Path(self.knowledge_dir).mkdir(parents=True, exist_ok=True)
            if self.knowledge_index_backend == "sqlite_fts5":
                Path(self.knowledge_index_path).parent.mkdir(parents=True, exist_ok=True)
        if self.skills_enabled:
            Path(self.skills_dir).mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    return Settings()
