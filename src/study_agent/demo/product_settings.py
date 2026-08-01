"""Private settings and runtime-only model credential composition."""

from __future__ import annotations

import os
from collections.abc import Iterator, Mapping
from threading import RLock
from typing import cast

from study_agent.domain._validation import JsonObject

from .ui_application import UiApplicationPort, UiRequestError

MAX_RUNTIME_CREDENTIAL_CHARS = 4096
LUNA_MODEL_LABEL = "GPT-5.6 Luna"
LUNA_ADAPTER_ID = "openai-gpt-5.6-luna"


class RuntimeCredentialStore(Mapping[str, str]):
    """Thread-safe, non-persistent ``OPENAI_API_KEY`` environment overlay.

    The wrapped environment is read lazily, so constructing the private shell
    does not enumerate or copy process credentials.  The only writable key is
    the adapter's fixed ``OPENAI_API_KEY`` binding.
    """

    def __init__(self, base: Mapping[str, str] | None = None) -> None:
        self._base = os.environ if base is None else base
        self._lock = RLock()
        self._override: str | None = None

    @property
    def configured(self) -> bool:
        with self._lock:
            return self._override is not None or bool(self._base.get("OPENAI_API_KEY"))

    def replace(self, api_key: str) -> None:
        if not isinstance(api_key, str) or not api_key.strip():
            raise ValueError("api_key must be non-empty")
        normalized = api_key.strip()
        if len(normalized) > MAX_RUNTIME_CREDENTIAL_CHARS:
            raise ValueError("api_key exceeds the configured bound")
        with self._lock:
            self._override = normalized

    def remove(self) -> None:
        with self._lock:
            self._override = None

    def get(self, key: str, default: str | None = None) -> str | None:
        if key != "OPENAI_API_KEY":
            return self._base.get(key, default)
        with self._lock:
            if self._override is not None:
                return self._override
        value = self._base.get(key)
        return value if isinstance(value, str) and value else default

    def __getitem__(self, key: str) -> str:
        value = self.get(key)
        if value is None:
            raise KeyError(key)
        return value

    def __iter__(self) -> Iterator[str]:
        # Mapping consumers only need this for generic protocol support.  Do
        # not materialize values or expose the runtime override through repr.
        return iter(self._base)

    def __len__(self) -> int:
        return len(self._base)

    def __repr__(self) -> str:  # pragma: no cover - defensive redaction
        return "RuntimeCredentialStore(<redacted>)"


class PrivateSettingsApplication(UiApplicationPort):
    """Add safe account/model settings to an existing UI application."""

    mode = "private"

    def __init__(
        self,
        delegate: UiApplicationPort,
        *,
        credentials: RuntimeCredentialStore | None = None,
        account_label: str = "Proprietario",
    ) -> None:
        if (
            not isinstance(account_label, str)
            or not account_label.strip()
            or len(account_label) > 128
        ):
            raise ValueError("account_label must be bounded non-empty text")
        self._delegate = delegate
        self.credentials = (
            credentials if credentials is not None else RuntimeCredentialStore()
        )
        self._account_label = account_label.strip()

    @property
    def delegate(self) -> UiApplicationPort:
        return self._delegate

    def get(self, path: str) -> JsonObject:
        if path in {"/api/v1/settings", "/api/v1/settings/account", "/api/v1/account"}:
            return self._settings()
        payload = self._delegate.get(path)
        if path in {"/api/v1/bootstrap", "/api/v1/session"}:
            payload = dict(payload)
            payload["mode"] = "private"
        return payload

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        if path in {
            "/api/v1/settings/api-key",
            "/api/v1/settings/model-key",
            "/api/v1/settings/model/credential",
            "/api/v1/settings/credentials/openai",
        }:
            return self._replace_key(command)
        if path in {
            "/api/v1/settings/api-key/remove",
            "/api/v1/settings/model-key/remove",
            "/api/v1/settings/model/credential/remove",
            "/api/v1/settings/credentials/openai/remove",
        }:
            if command:
                raise UiRequestError("remove command must be empty", status_code=400)
            self.credentials.remove()
            return self._credential_result("removed")
        return self._delegate.post(path, command)

    def _replace_key(self, command: Mapping[str, object]) -> JsonObject:
        if set(command) != {"api_key"} or not isinstance(command.get("api_key"), str):
            raise UiRequestError("api_key is required and write-only", status_code=400)
        try:
            self.credentials.replace(cast(str, command["api_key"]))
        except ValueError as error:
            raise UiRequestError(str(error), status_code=400) from None
        return self._credential_result("configured")

    def _settings(self) -> JsonObject:
        return {
            "schema_version": 1,
            "account": {"label": self._account_label, "mode": "single_owner"},
            "data": {
                "repository": {"configured": True, "kind": "local_repository"},
                "persistence": "local_repository",
                "restart_behavior": "sessions and runtime credentials clear on restart",
            },
            "model": {
                "provider": "openai",
                "model": LUNA_MODEL_LABEL,
                "adapter_id": LUNA_ADAPTER_ID,
                "credential_configured": self.credentials.configured,
            },
            "privacy": {
                "credential_storage": "runtime_only",
                "browser_storage": "none",
                "secret_echo": False,
            },
        }

    def _credential_result(self, status: str) -> JsonObject:
        return {
            "schema_version": 1,
            "status": status,
            "provider": "openai",
            "model": LUNA_MODEL_LABEL,
            "credential_configured": self.credentials.configured,
        }


__all__ = [
    "LUNA_ADAPTER_ID",
    "LUNA_MODEL_LABEL",
    "MAX_RUNTIME_CREDENTIAL_CHARS",
    "PrivateSettingsApplication",
    "RuntimeCredentialStore",
]
