from __future__ import annotations

import threading
from collections.abc import Mapping

import pytest

from study_agent.demo.product_settings import (
    LUNA_ADAPTER_ID,
    LUNA_MODEL_LABEL,
    MAX_RUNTIME_CREDENTIAL_CHARS,
    PrivateSettingsApplication,
    RuntimeCredentialStore,
)
from study_agent.demo.ui_application import UiRequestError
from study_agent.domain._validation import JsonObject

SECRET = "sk-test-runtime-secret"


class _Delegate:
    mode = "public_demo"

    def __init__(self) -> None:
        self.get_calls: list[str] = []
        self.post_calls: list[tuple[str, Mapping[str, object]]] = []
        self.payloads: dict[str, JsonObject] = {
            "/api/v1/bootstrap": {"schema_version": 1, "mode": "public_demo"},
            "/api/v1/session": {"schema_version": 1, "mode": "public_demo"},
            "/api/v1/materials": {"schema_version": 1, "status": "ready"},
        }

    def get(self, path: str) -> JsonObject:
        self.get_calls.append(path)
        return self.payloads[path]

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        self.post_calls.append((path, command))
        return {"delegated": True, "path": path}


def test_runtime_store_overlay_reverts_to_base_without_secret_repr_leak() -> None:
    store = RuntimeCredentialStore({"OPENAI_API_KEY": "base", "OTHER": "value"})

    assert store.configured
    assert store.get("OPENAI_API_KEY") == "base"
    store.replace(SECRET)
    assert store.configured
    assert store["OPENAI_API_KEY"] == SECRET
    assert store.get("OTHER") == "value"
    assert SECRET not in repr(store)
    assert repr(store) == "RuntimeCredentialStore(<redacted>)"
    store.remove()
    assert store["OPENAI_API_KEY"] == "base"


def test_runtime_store_trims_accidental_paste_whitespace_before_adapter_use() -> None:
    store = RuntimeCredentialStore()

    store.replace("  sk-pasted-key\n")

    assert store["OPENAI_API_KEY"] == "sk-pasted-key"


def test_runtime_store_empty_base_uses_mapping_defaults_and_preserves_shape() -> None:
    store = RuntimeCredentialStore({"OTHER": "value"})

    assert not store.configured
    assert store.get("OPENAI_API_KEY") is None
    assert store.get("OPENAI_API_KEY", "fallback") == "fallback"
    assert store.get("OTHER") == "value"
    assert set(store) == {"OTHER"}
    assert len(store) == 1
    with pytest.raises(KeyError):
        _ = store["OPENAI_API_KEY"]


def test_settings_preserves_an_explicit_empty_isolated_credential_store() -> None:
    store = RuntimeCredentialStore({})
    app = PrivateSettingsApplication(_Delegate(), credentials=store)

    assert app.credentials is store
    assert not app.credentials.configured


@pytest.mark.parametrize("value", ("", "   ", "x" * (MAX_RUNTIME_CREDENTIAL_CHARS + 1)))
def test_runtime_store_rejects_empty_or_oversized_credentials(value: str) -> None:
    with pytest.raises(ValueError):
        RuntimeCredentialStore().replace(value)


def test_runtime_store_observes_consistent_values_during_concurrent_replacement() -> None:
    store = RuntimeCredentialStore()
    values = {"key-a", "key-b"}
    errors: list[BaseException] = []

    def writer(value: str) -> None:
        try:
            for _ in range(100):
                store.replace(value)
                assert store.get("OPENAI_API_KEY") in values
        except BaseException as error:  # pragma: no cover - only reports a race
            errors.append(error)

    threads = [threading.Thread(target=writer, args=(value,)) for value in values]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert errors == []
    assert store.get("OPENAI_API_KEY") in values


def test_settings_application_exposes_exact_safe_settings_dto_on_canonical_routes() -> None:
    delegate = _Delegate()
    app = PrivateSettingsApplication(
        delegate,
        credentials=RuntimeCredentialStore(),
        account_label="  Owner  ",
    )

    expected = {
        "schema_version": 1,
        "account": {"label": "Owner", "mode": "single_owner"},
        "data": {
            "repository": {"configured": True, "kind": "local_repository"},
            "persistence": "local_repository",
            "restart_behavior": "sessions and runtime credentials clear on restart",
        },
        "model": {
            "provider": "openai",
            "model": LUNA_MODEL_LABEL,
            "adapter_id": LUNA_ADAPTER_ID,
            "credential_configured": False,
        },
        "privacy": {
            "credential_storage": "runtime_only",
            "browser_storage": "none",
            "secret_echo": False,
        },
    }
    for path in ("/api/v1/settings", "/api/v1/settings/account", "/api/v1/account"):
        assert app.get(path) == expected
    assert delegate.get_calls == []
    assert SECRET not in repr(expected)


def test_settings_application_marks_only_bootstrap_and_session_as_private() -> None:
    delegate = _Delegate()
    app = PrivateSettingsApplication(delegate)

    bootstrap = app.get("/api/v1/bootstrap")
    session = app.get("/api/v1/session")
    materials = app.get("/api/v1/materials")
    assert bootstrap == {"schema_version": 1, "mode": "private"}
    assert session == {"schema_version": 1, "mode": "private"}
    assert materials == delegate.payloads["/api/v1/materials"]
    assert delegate.get_calls == ["/api/v1/bootstrap", "/api/v1/session", "/api/v1/materials"]


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/settings/api-key",
        "/api/v1/settings/model-key",
        "/api/v1/settings/model/credential",
        "/api/v1/settings/credentials/openai",
    ),
)
def test_credential_replace_is_write_only_on_all_supported_routes(path: str) -> None:
    credentials = RuntimeCredentialStore()
    app = PrivateSettingsApplication(_Delegate(), credentials=credentials)

    result = app.post(path, {"api_key": SECRET})

    assert result == {
        "schema_version": 1,
        "status": "configured",
        "provider": "openai",
        "model": LUNA_MODEL_LABEL,
        "credential_configured": True,
    }
    assert SECRET not in repr(result)
    assert SECRET not in str(result)
    assert credentials["OPENAI_API_KEY"] == SECRET


@pytest.mark.parametrize(
    "path",
    (
        "/api/v1/settings/api-key/remove",
        "/api/v1/settings/model-key/remove",
        "/api/v1/settings/model/credential/remove",
        "/api/v1/settings/credentials/openai/remove",
    ),
)
def test_credential_remove_is_write_only_and_returns_safe_status(path: str) -> None:
    credentials = RuntimeCredentialStore({"OPENAI_API_KEY": "base"})
    app = PrivateSettingsApplication(_Delegate(), credentials=credentials)
    credentials.replace(SECRET)

    result = app.post(path, {})

    assert result["status"] == "removed"
    assert result["credential_configured"] is True
    assert credentials["OPENAI_API_KEY"] == "base"
    assert SECRET not in str(result)


@pytest.mark.parametrize("command", ({}, {"api_key": SECRET, "extra": True}, {"api_key": 42}))
def test_credential_replace_rejects_non_write_only_commands(command: Mapping[str, object]) -> None:
    app = PrivateSettingsApplication(_Delegate())

    with pytest.raises(UiRequestError, match="write-only"):
        app.post("/api/v1/settings/model/credential", command)


def test_credential_remove_requires_an_empty_command_and_other_routes_delegate() -> None:
    delegate = _Delegate()
    app = PrivateSettingsApplication(delegate)

    with pytest.raises(UiRequestError, match="empty"):
        app.post("/api/v1/settings/model/credential/remove", {"unexpected": True})
    assert delegate.post_calls == []

    command = {"request_id": "r1"}
    assert app.post("/api/v1/session/turns", command) == {
        "delegated": True,
        "path": "/api/v1/session/turns",
    }
    assert delegate.post_calls == [("/api/v1/session/turns", command)]
