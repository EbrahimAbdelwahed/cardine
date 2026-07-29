from __future__ import annotations

import json
from collections.abc import Mapping

import pytest

from study_agent.demo.browser import BrowserSurface, _require_bind_host, create_server
from study_agent.domain._validation import JsonValue


def _journey(entry: str) -> dict[str, object]:
    return {
        "learner_entry": entry,
        "status": "recovered",
        "status_trace": ({"step": 1, "status": "completed", "detail": "Grounded"},),
        "source_state": {"fixture": "notes.md", "evidence": ("A fact",)},
        "evidence_refresh_sequence": 2,
        "discovered_capabilities": ("explain_concept",),
        "parity": True,
    }


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    assert isinstance(value, Mapping)
    return value


def test_browser_surface_projects_the_existing_journey_without_new_state() -> None:
    surface = BrowserSurface(_journey)

    first = surface.state("  Explain valves  ")
    second = surface.state("Explain valves")

    assert first == second
    assert first["learner_entry"] == "Explain valves"
    assert _mapping(first["conversation"])["status_trace"] == (
        {"step": 1, "status": "completed", "detail": "Grounded"},
    )
    assert _mapping(first["material"])["fixture"] == "notes.md"
    assert _mapping(first["evidence"])["sequence"] == 2
    assert _mapping(first["conflict"])["status"] == "clear"
    assert _mapping(first["due_review"])["status"] == "unavailable"
    assert first["parity"] is True


def test_browser_surface_uses_conflicts_and_due_review_when_a_host_view_has_them() -> None:
    def journey(entry: str) -> dict[str, object]:
        result = _journey(entry)
        result["conflict"] = {"status": "conflicted", "message": "Goal differs"}
        result["due_review"] = {
            "status": "needs_review",
            "items": ({"label": "Aortic valve"},),
            "message": "One review is due.",
        }
        return result

    payload = BrowserSurface(journey).state("review this")

    assert payload["conflict"] == {"status": "conflicted", "message": "Goal differs"}
    due_review = _mapping(payload["due_review"])
    assert due_review["status"] == "needs_review"
    assert due_review["items"] == ({"label": "Aortic valve"},)


def test_browser_input_is_bounded_and_server_is_local_only() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        BrowserSurface(_journey).state("   ")
    with pytest.raises(ValueError, match="text bound"):
        BrowserSurface(_journey).state("x" * 4_001)
    with pytest.raises(ValueError, match="public-demo"):
        create_server("0.0.0.0", 0, journey=_journey)


def test_public_demo_is_the_only_mode_allowed_to_bind_all_interfaces() -> None:
    _require_bind_host("127.0.0.1", public_demo=False)
    _require_bind_host("127.0.0.1", public_demo=True)
    _require_bind_host("0.0.0.0", public_demo=True)

    with pytest.raises(ValueError, match="public-demo"):
        _require_bind_host("0.0.0.0", public_demo=False)
    with pytest.raises(ValueError, match="bind host"):
        _require_bind_host("192.0.2.10", public_demo=True)

    with pytest.raises(ValueError, match="fixed sanitized journey"):
        create_server(
            "127.0.0.1",
            0,
            journey=_journey,
            public_demo=True,
        )


def test_browser_page_bytes_are_static_and_accessible() -> None:
    page = BrowserSurface(_journey).page()

    assert page == BrowserSurface(_journey).page()
    decoded = page.decode("utf-8")
    for marker in (
        '<textarea id="entry"',
        'aria-labelledby="conversation-heading"',
        'aria-labelledby="material-heading"',
        'aria-labelledby="evidence-heading"',
        'aria-labelledby="conflict-heading"',
        'aria-labelledby="review-heading"',
        'id="entry-form"',
    ):
        assert marker in decoded
    assert ".meta { color: var(--muted); font-size: .9rem; overflow-wrap: anywhere; }" in decoded
    assert BrowserSurface(_journey).asset("browser.css").startswith(b":root")
    assert b'"use strict";' in BrowserSurface(_journey).asset("browser.js")

    with pytest.raises(ValueError, match="unknown browser asset"):
        BrowserSurface(_journey).asset("../secret")


def test_browser_payload_is_json_deterministic() -> None:
    payload = BrowserSurface(_journey).state("same")

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=list)
    repeated = json.dumps(
        BrowserSurface(_journey).state("same"),
        sort_keys=True,
        separators=(",", ":"),
        default=list,
    )

    assert encoded == repeated


def test_browser_surface_exposes_versioned_referto_api_without_transport_state() -> None:
    surface = BrowserSurface(_journey)

    bootstrap = surface.api_get("/api/v1/bootstrap")
    receipt = surface.api_post(
        "/api/v1/session/turns",
        {
            "schema_version": 1,
            "request_id": "browser-request-1",
            "expected_sequence": 2,
            "payload": {"content": "Explain valves"},
        },
    )

    assert bootstrap["mode"] == "public_demo"
    assert receipt["request_id"] == "browser-request-1"
    assert receipt["status"] == "demo_completed"
