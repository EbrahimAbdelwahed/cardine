from __future__ import annotations

import json
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from http.client import HTTPConnection
from threading import Thread
from typing import cast

import pytest

from cardine.demo.browser import (
    MIN_LOCAL_OWNER_PASSWORD_CHARS,
    PREVIEW_RUNTIME_ID,
    BrowserSurface,
    _require_bind_host,
    create_server,
)
from cardine.demo.product_settings import (
    PrivateSettingsApplication,
    RuntimeCredentialStore,
)
from cardine.demo.ui_application import SourceDocumentView, UiRequestError
from study_agent.domain._validation import JsonObject


class _RepositoryApplication:
    mode = "local_repository"

    def get(self, path: str) -> JsonObject:
        if path != "/api/v1/bootstrap":
            raise AssertionError(path)
        return {"schema_version": 1, "mode": self.mode}

    def post(self, path: str, command: object) -> JsonObject:
        assert path == "/api/v1/session/turns"
        assert isinstance(command, dict)
        return {"schema_version": 1, "status": "committed"}

    def read_source_document(
        self, source_id: str, revision_id: str
    ) -> SourceDocumentView:
        assert (source_id, revision_id) == ("source-one", "revision-one")
        return SourceDocumentView(
            title="Lezione uno",
            viewer_kind="markdown",
            media_type="text/markdown; charset=utf-8",
            content=b"# Lezione uno",
        )


def test_browser_requires_a_repository_application_and_never_constructs_demo_state() -> None:
    surface = BrowserSurface(_RepositoryApplication())
    assert surface.repository_backed is True
    assert surface.api_get("/api/v1/bootstrap")["mode"] == "local_repository"
    assert not hasattr(surface, "state")
    with pytest.raises(TypeError):
        BrowserSurface()  # type: ignore[call-arg]


def test_non_loopback_requires_private_production() -> None:
    _require_bind_host("127.0.0.1")
    _require_bind_host("0.0.0.0", private_production=True)
    with pytest.raises(ValueError, match="private production"):
        _require_bind_host("0.0.0.0")
    with pytest.raises(ValueError, match="bind host"):
        _require_bind_host("192.0.2.10")
    with pytest.raises(ValueError, match="private production"):
        create_server("0.0.0.0", 0, ui_application=_RepositoryApplication())
    with pytest.raises(ValueError, match="private production"):
        create_server(
            "0.0.0.0",
            0,
            ui_application=_RepositoryApplication(),
            settings_application=PrivateSettingsApplication(
                _RepositoryApplication(),
                mode="local_repository",
            ),
        )


def test_browser_page_bytes_are_static_and_accessible() -> None:
    page = BrowserSurface(_RepositoryApplication()).page()

    assert page == BrowserSurface(_RepositoryApplication()).page()
    decoded = page.decode("utf-8")
    for marker in (
        '<textarea id="entry"',
        'id="entry-form"',
        'id="main-content"',
    ):
        assert marker in decoded
    # The shell ships no scaffolding: no placeholder sections and no style
    # rule hidden inside an HTML comment to keep an old assertion green.
    assert "<template" not in decoded
    assert ".meta {" not in decoded
    assert "smoke" not in decoded
    # An error must have somewhere visible to land that survives a re-render.
    assert 'id="global-alert"' in decoded
    assert b":root" in BrowserSurface(_RepositoryApplication()).asset("browser.css")
    assert b'"use strict";' in BrowserSurface(_RepositoryApplication()).asset("browser.js")
    assert b'data-auth-setup' in BrowserSurface(_RepositoryApplication()).asset("browser.js")
    assert b".ai-loading" in BrowserSurface(_RepositoryApplication()).asset("ai-primitives.css")
    assert b"CardineAI" in BrowserSurface(_RepositoryApplication()).asset("ai-primitives.js")
    assert BrowserSurface(_RepositoryApplication()).asset("icons/plus.svg").startswith(b"<svg")

    with pytest.raises(ValueError, match="unknown browser asset"):
        BrowserSurface(_RepositoryApplication()).asset("../secret")
    with pytest.raises(ValueError, match="unknown browser asset"):
        BrowserSurface(_RepositoryApplication()).asset("icons/../browser.js")


def test_browser_surface_exposes_only_versioned_repository_api() -> None:
    surface = BrowserSurface(_RepositoryApplication())
    assert surface.api_post("/api/v1/session/turns", {})["status"] == "committed"


def test_local_repository_settings_are_same_origin_write_only_and_password_free() -> None:
    credentials = RuntimeCredentialStore()
    settings = PrivateSettingsApplication(
        _RepositoryApplication(),
        credentials=credentials,
        mode="local_repository",
    )
    try:
        server = create_server(
            "127.0.0.1",
            0,
            ui_application=_RepositoryApplication(),
            settings_application=settings,
        )
    except PermissionError as error:
        pytest.skip(f"local sockets are unavailable: {error}")
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = cast(tuple[str, int], server.server_address)
    origin = f"http://{host}:{port}"
    secret = "sk-local-browser-secret"

    def request(
        method: str,
        path: str,
        *,
        payload: Mapping[str, object] | None = None,
        request_origin: str | None = None,
    ) -> tuple[int, object]:
        connection = HTTPConnection(host, port, timeout=2)
        body = None if payload is None else json.dumps(payload).encode("utf-8")
        headers = {"Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(body))
        if request_origin is not None:
            headers["Origin"] = request_origin
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = json.loads(response.read().decode("utf-8"))
        connection.close()
        return response.status, result

    try:
        status, settings_payload = request("GET", "/api/v1/settings")
        assert status == 200
        assert cast(dict[str, object], settings_payload)["model"]

        missing_origin_status, _ = request(
            "POST",
            "/api/v1/settings/model/credential",
            payload={"api_key": secret},
        )
        cross_origin_status, _ = request(
            "POST",
            "/api/v1/settings/model/credential",
            payload={"api_key": secret},
            request_origin="http://evil.example",
        )
        assert missing_origin_status == 403
        assert cross_origin_status == 403
        assert not credentials.configured

        saved_status, saved_payload = request(
            "POST",
            "/api/v1/settings/model/credential",
            payload={"api_key": secret},
            request_origin=origin,
        )
        assert saved_status == 200
        assert secret not in str(saved_payload)
        assert credentials.get("OPENAI_API_KEY") == secret

        read_status, read_payload = request("GET", "/api/v1/settings")
        assert read_status == 200
        assert cast(dict[str, object], read_payload)["model"]["credential_configured"] is True  # type: ignore[index]
        assert secret not in str(read_payload)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_browser_surface_reads_a_canonical_document_through_the_application() -> None:
    document = BrowserSurface(_RepositoryApplication()).api_source_document(
        "source-one", "revision-one"
    )

    assert document.title == "Lezione uno"
    assert document.viewer_kind == "markdown"
    assert document.content == b"# Lezione uno"


def test_loopback_owner_setup_activates_private_settings_without_exposing_a_secret() -> None:
    credentials = RuntimeCredentialStore(base={})
    surface = BrowserSurface(_RepositoryApplication(), runtime_credentials=credentials)
    surface.enable_local_owner_setup("http://127.0.0.1:8765")

    probe = surface.api_get("/api/v1/auth/session")
    assert probe == {
        "schema_version": 1,
        "mode": "setup",
        "authenticated": False,
        "setup_required": True,
    }
    with pytest.raises(ValueError, match="at least"):
        surface.configure_local_owner(
            "x" * (MIN_LOCAL_OWNER_PASSWORD_CHARS - 1),
            client_id="test",
        )

    session = surface.configure_local_owner("correct horse battery staple", client_id="test")
    assert surface.mode == "private"
    with pytest.raises(UiRequestError, match="authentication"):
        surface.api_source_document("source-one", "revision-one")
    assert surface.api_source_document(
        "source-one", "revision-one", session_token=session.session_token
    ).viewer_kind == "markdown"
    assert surface.api_get("/api/v1/auth/session", session_token=session.session_token) == {
        "schema_version": 1,
        "mode": "private",
        "authenticated": True,
        "csrf_token": session.csrf_token,
    }
    settings = surface.api_get("/api/v1/settings", session_token=session.session_token)
    model = cast(Mapping[str, object], settings["model"])
    assert model["credential_configured"] is False
    surface.api_post(
        "/api/v1/settings/model/credential",
        {"api_key": "test-runtime-key"},
        session_token=session.session_token,
        csrf_token=session.csrf_token,
    )
    assert credentials.configured is True
    assert credentials.get("OPENAI_API_KEY") == "test-runtime-key"
    assert "password" not in repr(surface.private_access).lower()


def test_local_owner_setup_is_atomic_and_one_time() -> None:
    surface = BrowserSurface(_RepositoryApplication())
    surface.enable_local_owner_setup("http://127.0.0.1:8765")

    def configure() -> bool:
        try:
            surface.configure_local_owner(
                "correct horse battery staple",
                client_id="race",
            )
        except Exception:
            return False
        return True

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: configure(), range(2)))
    assert sorted(results) == [False, True]


def test_local_owner_setup_is_loopback_only_and_cannot_be_combined_with_private_access() -> None:
    with pytest.raises(ValueError, match="private production"):
        create_server("0.0.0.0", 0, ui_application=_RepositoryApplication(), local_owner_setup=True)


def test_preview_runtime_marker_is_safe_and_versioned() -> None:
    assert PREVIEW_RUNTIME_ID.startswith("cardine-local-")
    assert "key" not in PREVIEW_RUNTIME_ID.lower()
    assert "secret" not in PREVIEW_RUNTIME_ID.lower()


def test_preview_diagnostics_expose_only_tutor_decisions(
    capsys: pytest.CaptureFixture[str],
) -> None:
    surface = BrowserSurface(_RepositoryApplication())

    surface.diagnostic("/api/v1/session/turns", 503, "tutor_execution_failed")
    surface.diagnostic("/api/v1/session/turns", 503, "unsafe provider response")

    diagnostics = surface.diagnostics()
    assert set(diagnostics) == {
        "schema_version",
        "latest_trace_id",
        "turn_traces",
        "retention",
    }
    assert diagnostics["turn_traces"] == ()
    output = capsys.readouterr().err
    assert "tutor_execution_failed" in output
    assert "unsafe provider response" not in output


def test_preview_diagnostics_keep_a_safe_model_failure_category(
    capsys: pytest.CaptureFixture[str],
) -> None:
    surface = BrowserSurface(_RepositoryApplication())

    surface.diagnostic("/api/v1/session/turns", 502, "tutor_endpoint_incompatible")

    assert "entries" not in surface.diagnostics()
    assert "tutor_endpoint_incompatible" in capsys.readouterr().err
