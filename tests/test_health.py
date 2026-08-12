from fastapi.testclient import TestClient

from agent_product.core.config import Settings
from agent_product.main import create_app


def test_live(client: TestClient) -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
    assert response.headers["X-Request-ID"]


def test_ready(client: TestClient) -> None:
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_app_starts_before_provider_key_is_configured(tmp_path) -> None:
    settings = Settings(
        app_env="test",
        ai_model="openai:gpt-4.1-mini",
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'no-key.db').as_posix()}",
    )
    app = create_app(settings=settings)

    with TestClient(app) as no_key_client:
        response = no_key_client.get("/health/live")

    assert response.status_code == 200
