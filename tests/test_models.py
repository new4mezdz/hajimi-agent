from pathlib import Path

from fastapi.testclient import TestClient
from pydantic import SecretStr
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.main import create_app


def _model_client(tmp_path: Path, **overrides: object) -> TestClient:
    settings = Settings(
        app_env="test",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'models.db').as_posix()}",
        support_demo_seed_enabled=False,
        **overrides,
    )
    return TestClient(create_app(settings=settings, model=TestModel()))


def test_lists_the_active_model_when_its_provider_is_configured(tmp_path: Path) -> None:
    with _model_client(
        tmp_path,
        ai_model="deepseek:deepseek-chat",
        deepseek_api_key=SecretStr("configured"),
    ) as client:
        response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == [
        {
            "provider": "deepseek",
            "model": "deepseek:deepseek-chat",
            "is_active": True,
        }
    ]


def test_hides_models_without_a_configured_provider_key(tmp_path: Path) -> None:
    with _model_client(tmp_path, ai_model="openai:gpt-4.1-mini") as client:
        response = client.get("/v1/models")

    assert response.status_code == 200
    assert response.json() == []
