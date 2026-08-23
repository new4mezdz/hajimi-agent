import json
from pathlib import Path

from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from agent_product.core.config import Settings
from agent_product.main import create_app
from agent_product.services.local_settings import LocalSettingsStore


def _protect(secret: str) -> str:
    return secret.encode("utf-8").hex()


def _unprotect(encrypted: str) -> str:
    return bytes.fromhex(encrypted).decode("utf-8")


def _base_settings(tmp_path: Path) -> Settings:
    return Settings(
        _env_file=None,
        app_env="test",
        ai_model="openai:gpt-4.1-mini",
        openai_api_key=None,
        deepseek_api_key=None,
        anthropic_api_key=None,
        database_url=f"sqlite+aiosqlite:///{(tmp_path / 'settings.db').as_posix()}",
        support_demo_seed_enabled=False,
    )


def _store(tmp_path: Path) -> LocalSettingsStore:
    return LocalSettingsStore(
        tmp_path / "agent-settings.json",
        protect=_protect,
        unprotect=_unprotect,
    )


def test_encrypted_settings_survive_a_new_store_instance(tmp_path: Path) -> None:
    base = _base_settings(tmp_path)
    store = _store(tmp_path)
    applied = store.update(
        base,
        provider="deepseek",
        model="deepseek:deepseek-chat",
        web_search_enabled=False,
        workspace_write_enabled=True,
        agent_instructions="Persist this configuration.",
        api_key="sk-persisted-secret",
        clear_api_key=False,
    )

    raw = store.path.read_text(encoding="utf-8")
    restored = _store(tmp_path).apply(base)
    public = _store(tmp_path).public(restored)

    assert "sk-persisted-secret" not in raw
    assert applied.deepseek_api_key is not None
    assert restored.deepseek_api_key is not None
    assert restored.deepseek_api_key.get_secret_value() == "sk-persisted-secret"
    assert public["configuredProviders"] == ["deepseek"]
    assert public["apiKeyPreviews"]["deepseek"] == "sk-pers••••••••"
    assert public["configuredModels"] == {"deepseek": "deepseek:deepseek-chat"}


def test_browser_settings_endpoint_saves_and_applies_configuration(tmp_path: Path) -> None:
    store = _store(tmp_path)
    app = create_app(
        settings=_base_settings(tmp_path),
        model=TestModel(call_tools=[], custom_output_text="Test response"),
        local_settings_store=store,
    )
    with TestClient(app) as client:
        response = client.put(
            "/v1/settings",
            json={
                "provider": "anthropic",
                "model": "anthropic:claude-test",
                "webSearchEnabled": False,
                "workspaceWriteEnabled": False,
                "agentInstructions": "Saved from browser mode.",
                "apiKey": "sk-ant-browser-secret",
                "clearApiKey": False,
            },
        )
        reloaded = client.get("/v1/settings")

    assert response.status_code == 200
    assert reloaded.status_code == 200
    assert response.json()["apiKeyPreviews"]["anthropic"] == "sk-ant-••••••••"
    assert reloaded.json()["model"] == "anthropic:claude-test"
    assert app.state.settings.ai_model == "anthropic:claude-test"
    assert json.loads(store.path.read_text(encoding="utf-8"))["version"] == 2
