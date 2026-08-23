from __future__ import annotations

import ctypes
import json
import os
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path
from typing import Any

from pydantic import SecretStr

from agent_product.core.config import Settings

APP_IDENTIFIER = "com.new4mezdz.hajimi-agent"
SETTINGS_FILE = "agent-settings.json"
PROVIDER_MODELS = {
    "openai": "openai:gpt-4.1-mini",
    "deepseek": "deepseek:deepseek-v4-flash",
    "anthropic": "anthropic:claude-sonnet-4-5",
}
PROVIDER_KEY_FIELDS = {
    "openai": "openai_api_key",
    "deepseek": "deepseek_api_key",
    "anthropic": "anthropic_api_key",
}


class LocalSettingsError(RuntimeError):
    pass


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_ubyte))]


def _input_blob(data: bytes) -> tuple[_DataBlob, ctypes.Array[ctypes.c_char]]:
    buffer = ctypes.create_string_buffer(data)
    blob = _DataBlob(
        len(data),
        ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)),
    )
    return blob, buffer


def protect_secret(secret: str) -> str:
    if os.name != "nt":
        raise LocalSettingsError("Secure API-key storage is currently available on Windows only")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptProtectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        wintypes.LPCWSTR,
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptProtectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, source_buffer = _input_blob(secret.encode("utf-8"))
    output = _DataBlob()
    if not crypt32.CryptProtectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(output),
    ):
        raise LocalSettingsError(f"Windows DPAPI encryption failed: {ctypes.WinError()}")
    del source_buffer
    try:
        return ctypes.string_at(output.pbData, output.cbData).hex()
    finally:
        kernel32.LocalFree(output.pbData)


def unprotect_secret(encrypted: str) -> str:
    if os.name != "nt":
        raise LocalSettingsError("Secure API-key storage is currently available on Windows only")
    try:
        encrypted_bytes = bytes.fromhex(encrypted)
    except ValueError as exc:
        raise LocalSettingsError("The encrypted API key is not valid hex") from exc

    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    crypt32.CryptUnprotectData.argtypes = [
        ctypes.POINTER(_DataBlob),
        ctypes.POINTER(wintypes.LPWSTR),
        ctypes.POINTER(_DataBlob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(_DataBlob),
    ]
    crypt32.CryptUnprotectData.restype = wintypes.BOOL
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p

    source, source_buffer = _input_blob(encrypted_bytes)
    output = _DataBlob()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(source),
        None,
        None,
        None,
        None,
        0x1,
        ctypes.byref(output),
    ):
        raise LocalSettingsError(f"Windows DPAPI decryption failed: {ctypes.WinError()}")
    del source_buffer
    try:
        return ctypes.string_at(output.pbData, output.cbData).decode("utf-8")
    finally:
        kernel32.LocalFree(output.pbData)


def default_settings_path() -> Path:
    override = os.getenv("AGENT_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        root = Path(os.getenv("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        root = Path(os.getenv("XDG_CONFIG_HOME", Path.home() / ".config"))
    return root / APP_IDENTIFIER / SETTINGS_FILE


def masked_secret(secret: str) -> str:
    value = secret.strip()
    requested_visible = 7 if value.startswith("sk-") else 4
    visible = max(1, len(value) // 2) if len(value) <= requested_visible else requested_visible
    return f"{value[:visible]}••••••••"


class LocalSettingsStore:
    def __init__(
        self,
        path: Path | None = None,
        *,
        protect: Callable[[str], str] = protect_secret,
        unprotect: Callable[[str], str] = unprotect_secret,
    ) -> None:
        self.path = path or default_settings_path()
        self._protect = protect
        self._unprotect = unprotect

    @property
    def exists(self) -> bool:
        return self.path.is_file()

    def _read(self) -> dict[str, Any]:
        if not self.exists:
            return {}
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise LocalSettingsError(f"Could not read local Agent settings: {exc}") from exc
        if not isinstance(value, dict):
            raise LocalSettingsError("Local Agent settings must be a JSON object")
        return value

    def _normalize(self, raw: dict[str, Any], base: Settings) -> dict[str, Any]:
        provider = raw.get("provider")
        if provider not in PROVIDER_MODELS:
            candidate = base.ai_model.partition(":")[0]
            provider = candidate if candidate in PROVIDER_MODELS else "openai"
        model = raw.get("model")
        if not isinstance(model, str) or not model.startswith(f"{provider}:"):
            model = (
                base.ai_model
                if base.ai_model.startswith(f"{provider}:")
                else PROVIDER_MODELS[provider]
            )

        models = raw.get("models")
        models = dict(models) if isinstance(models, dict) else {}
        models = {
            key: value
            for key, value in models.items()
            if key in PROVIDER_MODELS
            and isinstance(value, str)
            and value.startswith(f"{key}:")
        }
        models[provider] = model

        encrypted = raw.get("encryptedApiKeys")
        encrypted = dict(encrypted) if isinstance(encrypted, dict) else {}
        encrypted = {
            key: value
            for key, value in encrypted.items()
            if key in PROVIDER_MODELS and isinstance(value, str) and value
        }
        for key in encrypted:
            models.setdefault(key, PROVIDER_MODELS[key])

        normalized = dict(raw)
        normalized.update(
            {
                "version": 2,
                "provider": provider,
                "model": model,
                "models": models,
                "webSearchEnabled": bool(raw.get("webSearchEnabled", base.web_search_enabled)),
                "workspaceWriteEnabled": bool(
                    raw.get("workspaceWriteEnabled", base.workspace_write_enabled)
                ),
                "agentInstructions": str(
                    raw.get("agentInstructions", base.agent_instructions)
                ).strip(),
                "encryptedApiKeys": encrypted,
            }
        )
        return normalized

    def _configured_secrets(
        self,
        raw: dict[str, Any],
        base: Settings,
    ) -> dict[str, str]:
        configured: dict[str, str] = {}
        encrypted = raw["encryptedApiKeys"]
        for provider, field in PROVIDER_KEY_FIELDS.items():
            value: str | None = None
            if provider in encrypted:
                try:
                    value = self._unprotect(encrypted[provider]).strip()
                except (LocalSettingsError, ValueError, UnicodeDecodeError):
                    value = None
            if not value:
                fallback = getattr(base, field)
                value = (
                    fallback.get_secret_value().strip()
                    if isinstance(fallback, SecretStr)
                    else None
                )
            if value:
                configured[provider] = value
        return configured

    def public(self, base: Settings) -> dict[str, Any]:
        raw = self._normalize(self._read(), base)
        configured = self._configured_secrets(raw, base)
        models = {
            provider: raw["models"].get(provider, PROVIDER_MODELS[provider])
            for provider in configured
        }
        return {
            "provider": raw["provider"],
            "model": raw["model"],
            "configuredModels": models,
            "webSearchEnabled": raw["webSearchEnabled"],
            "workspaceWriteEnabled": raw["workspaceWriteEnabled"],
            "agentInstructions": raw["agentInstructions"],
            "apiKeyConfigured": raw["provider"] in configured,
            "apiKeyPreviews": {
                provider: masked_secret(secret) for provider, secret in configured.items()
            },
            "configuredProviders": sorted(configured),
            "secureStorage": os.name == "nt" or self._protect is not protect_secret,
        }

    def apply(self, base: Settings) -> Settings:
        if not self.exists:
            return base
        raw = self._normalize(self._read(), base)
        configured = self._configured_secrets(raw, base)
        values = base.model_dump()
        values.update(
            {
                "ai_model": raw["model"],
                "web_search_enabled": raw["webSearchEnabled"],
                "workspace_write_enabled": raw["workspaceWriteEnabled"],
                "agent_instructions": raw["agentInstructions"],
            }
        )
        for provider, secret in configured.items():
            values[PROVIDER_KEY_FIELDS[provider]] = SecretStr(secret)
        return Settings(**values)

    def update(
        self,
        base: Settings,
        *,
        provider: str,
        model: str,
        web_search_enabled: bool,
        workspace_write_enabled: bool,
        agent_instructions: str,
        api_key: str | None,
        clear_api_key: bool,
    ) -> Settings:
        raw = self._normalize(self._read(), base)
        raw.update(
            {
                "version": 2,
                "provider": provider,
                "model": model,
                "webSearchEnabled": web_search_enabled,
                "workspaceWriteEnabled": workspace_write_enabled,
                "agentInstructions": agent_instructions.strip(),
            }
        )
        raw["models"][provider] = model
        if clear_api_key:
            raw["encryptedApiKeys"].pop(provider, None)
        elif api_key and api_key.strip():
            raw["encryptedApiKeys"][provider] = self._protect(api_key.strip())

        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".json.tmp")
        try:
            temporary.write_text(
                json.dumps(raw, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            os.replace(temporary, self.path)
        except OSError as exc:
            raise LocalSettingsError(f"Could not save local Agent settings: {exc}") from exc
        return self.apply(base)
