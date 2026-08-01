from __future__ import annotations

from collections.abc import Mapping

import pytest

from study_agent.demo.ui_application import UiRequestError
from study_agent.domain._validation import JsonObject, JsonValue

# This file is retained temporarily so downstream branches can see which
# stateless contract disappeared.  Its fixtures are intentionally not a
# runtime path any longer; repository-backed coverage lives in the Cardine
# integration and E2E suites.
pytestmark = pytest.mark.skip(reason="the stateless Cardine demo was removed")


def _journey(entry: str) -> JsonObject:
    return {
        "learner_entry": entry,
        "status": "recovered",
        "status_trace": (
            {"step": 1, "status": "completed", "detail": "Grounded explanation"},
            {"step": 2, "status": "suspended", "detail": "Choose a valve"},
        ),
        "material": {
            "fixture": "heart-valves.md",
            "title": "Valvole cardiache — materiale di esempio",
            "checksum_sha256": "a" * 64,
            "byte_size": 42,
            "evidence": ("Aortic evidence", "Pulmonary evidence"),
        },
        "context_state": {
            "initial_sequence": 1,
            "refreshed_sequence": 2,
            "selected_focus": "aortic valve",
        },
        "evidence_sequence": 2,
        "capabilities": ("explain_concept",),
        "due_review": {
            "status": "unavailable",
            "items": (),
            "message": "Recall is not configured.",
        },
        "parity": True,
    }


def _mapping(value: JsonValue) -> Mapping[str, JsonValue]:
    assert isinstance(value, Mapping)
    return value


def test_demo_bootstrap_is_honest_about_available_features() -> None:
    app = DemoUiApplication(_journey)

    bootstrap = app.get("/api/v1/bootstrap")

    assert bootstrap["schema_version"] == 1
    assert bootstrap["mode"] == "public_demo"
    assert bootstrap["high_water_sequence"] == 2
    assert bootstrap["shell_status"] == "recovered"
    assert _mapping(bootstrap["course"])["title"] == "Valvole cardiache"
    assert _mapping(bootstrap["features"]) == {
        "tutor": True,
        "artifacts": False,
        "assessments": False,
        "evidence": True,
        "recall": False,
        "context_resolution": False,
        "exam_plan": False,
    }
    assert _mapping(bootstrap["counts"]) == {
        "pending_proposals": 0,
        "due_reviews": 0,
        "context_conflicts": 0,
    }


@pytest.mark.parametrize(
    ("path", "status"),
    (
        ("/api/v1/session", "recovered"),
        ("/api/v1/materials", "ready"),
        ("/api/v1/artifacts", "unavailable"),
        ("/api/v1/assessments", "unavailable"),
        ("/api/v1/evidence", "ready"),
        ("/api/v1/recall/due", "unavailable"),
        ("/api/v1/context/conflicts", "ready"),
        ("/api/v1/plan", "unavailable"),
    ),
)
def test_demo_read_routes_expose_real_or_explicitly_unavailable_state(
    path: str, status: str
) -> None:
    app = DemoUiApplication(_journey)

    payload = app.get(path)

    assert payload["schema_version"] == 1
    assert payload["status"] == status


def test_demo_session_turn_is_stateless_bounded_and_clearly_labelled() -> None:
    app = DemoUiApplication(_journey)
    command: JsonObject = {
        "schema_version": 1,
        "request_id": "request-123",
        "expected_sequence": 2,
        "payload": {"content": "  Explain the aortic valve  "},
    }

    receipt = app.post("/api/v1/session/turns", command)
    repeated = app.post("/api/v1/session/turns", command)

    assert receipt == repeated
    assert receipt["status"] == "demo_completed"
    assert receipt["request_id"] == "request-123"
    assert receipt["high_water_sequence"] == 2
    result = _mapping(receipt["result"])
    assert result["learner_entry"] == "Explain the aortic valve"
    assert result["persistence"] == "stateless_public_demo"
    assert app.get("/api/v1/session")["learner_entry"] != result["learner_entry"]


@pytest.mark.parametrize(
    "command",
    (
        {},
        {"schema_version": 2, "request_id": "r", "expected_sequence": 2, "payload": {}},
        {
            "schema_version": 1,
            "request_id": "",
            "expected_sequence": 2,
            "payload": {"content": "hello"},
        },
        {
            "schema_version": 1,
            "request_id": "r",
            "expected_sequence": True,
            "payload": {"content": "hello"},
        },
        {
            "schema_version": 1,
            "request_id": "r",
            "expected_sequence": 2,
            "payload": {"content": "   "},
        },
    ),
)
def test_demo_session_turn_rejects_malformed_commands(command: JsonObject) -> None:
    app = DemoUiApplication(_journey)

    with pytest.raises(UiRequestError):
        app.post("/api/v1/session/turns", command)


def test_demo_rejects_unknown_routes_and_mutations() -> None:
    app = DemoUiApplication(_journey)

    with pytest.raises(UiRequestError, match="not found"):
        app.get("/api/v1/unknown")
    with pytest.raises(UiRequestError, match="not available"):
        app.post(
            "/api/v1/artifacts/revision-1/decisions",
            {
                "schema_version": 1,
                "request_id": "r",
                "expected_sequence": 2,
                "payload": {"decision": "accept"},
            },
        )


def test_unknown_read_route_is_rejected_before_running_the_fixture() -> None:
    calls = 0

    def journey(entry: str) -> JsonObject:
        nonlocal calls
        calls += 1
        return _journey(entry)

    app = DemoUiApplication(journey)

    with pytest.raises(UiRequestError, match="not found"):
        app.get("/api/v1/unknown")

    assert calls == 0


def test_demo_read_routes_share_one_cached_sanitized_fixture_result() -> None:
    calls = 0

    def journey(entry: str) -> JsonObject:
        nonlocal calls
        calls += 1
        return _journey(entry)

    app = DemoUiApplication(journey)

    assert app.get("/api/v1/bootstrap")["high_water_sequence"] == 2
    assert app.get("/api/v1/session")["high_water_sequence"] == 2
    assert app.get("/api/v1/materials")["high_water_sequence"] == 2
    assert calls == 1


@pytest.mark.parametrize(
    "command",
    (
        {
            "schema_version": 1,
            "request_id": "\ud800",
            "expected_sequence": 2,
            "payload": {"content": "hello"},
        },
        {
            "schema_version": 1,
            "request_id": "request",
            "expected_sequence": 2,
            "payload": {"content": "\ud800"},
        },
    ),
)
def test_demo_rejects_text_that_cannot_be_encoded_as_utf8(command: JsonObject) -> None:
    app = DemoUiApplication(_journey)

    with pytest.raises(UiRequestError, match="invalid"):
        app.post("/api/v1/session/turns", command)
