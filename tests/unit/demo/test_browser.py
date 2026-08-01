from __future__ import annotations

import pytest

from study_agent.demo.browser import (
    BrowserSurface,
    PREVIEW_RUNTIME_ID,
    _require_bind_host,
    create_server,
)
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


def test_browser_page_bytes_are_static_and_accessible() -> None:
    page = BrowserSurface(_RepositoryApplication()).page()

    assert page == BrowserSurface(_RepositoryApplication()).page()
    decoded = page.decode("utf-8")
    for marker in ('<textarea id="entry"', 'id="entry-form"', 'id="main-content"'):
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


def test_preview_runtime_marker_is_safe_and_versioned() -> None:
    assert PREVIEW_RUNTIME_ID.startswith("cardine-local-")
    assert "key" not in PREVIEW_RUNTIME_ID.lower()
    assert "secret" not in PREVIEW_RUNTIME_ID.lower()


def test_preview_diagnostics_are_bounded_and_redacted(capsys: pytest.CaptureFixture[str]) -> None:
    surface = BrowserSurface(_RepositoryApplication())

    surface.diagnostic("/api/v1/session/turns", 503, "tutor_execution_failed")
    surface.diagnostic("/api/v1/session/turns", 503, "unsafe provider response")

    entries = surface.diagnostics()["entries"]
    assert len(entries) == 2
    assert entries[0]["category"] == "tutor_execution_failed"
    assert entries[1]["category"] == "invalid_request"
    output = capsys.readouterr().err
    assert "tutor_execution_failed" in output
    assert "unsafe provider response" not in output


def test_preview_diagnostics_keep_a_safe_model_failure_category(
    capsys: pytest.CaptureFixture[str],
) -> None:
    surface = BrowserSurface(_RepositoryApplication())

    surface.diagnostic("/api/v1/session/turns", 502, "tutor_endpoint_incompatible")

    assert surface.diagnostics()["entries"][0]["category"] == "tutor_endpoint_incompatible"
    assert "tutor_endpoint_incompatible" in capsys.readouterr().err
