from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.main import create_app


@pytest.fixture
def client(tmp_path: Path) -> Iterator[TestClient]:
    database_path = tmp_path / "test.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        service_api_key=None,
    )
    app = create_app(
        settings=settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def secured_client(tmp_path: Path) -> Iterator[TestClient]:
    database_path = tmp_path / "secured.db"
    settings = Settings(
        app_env="test",
        ai_model="test",
        database_url=f"sqlite+aiosqlite:///{database_path.as_posix()}",
        service_api_key="secret",
    )
    app = create_app(
        settings=settings,
        model=TestModel(call_tools=[], custom_output_text="Test response"),
    )
    with TestClient(app) as test_client:
        yield test_client
