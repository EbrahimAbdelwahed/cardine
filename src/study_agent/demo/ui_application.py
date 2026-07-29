"""Versioned, transport-independent application surface for the Referto UI.

The public demo is deliberately stateless and sanitized.  Repository-backed
composition is a separate implementation of the same small ``get``/``post``
boundary; the browser server never needs authority over persistence adapters.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import cast

from study_agent.domain._validation import JsonObject

from .product_shell import MAX_LEARNER_ENTRY_CHARS, run_offline_shell_demo

DEFAULT_DEMO_ENTRY = "I have ten minutes. Help me understand heart valves."
DEMO_COURSE_ID = "cardine-demo"
DEMO_SESSION_ID = "heart-valves-demo"
DEMO_COURSE_TITLE = "Valvole cardiache"

DemoJourney = Callable[[str], Mapping[str, object]]


class UiRequestError(ValueError):
    """A versioned UI request cannot be served by this composition."""

    def __init__(self, message: str, *, status_code: int = 400) -> None:
        super().__init__(message)
        self.status_code = status_code


class DemoUiApplication:
    """Serve the sanitized reference trace through the product API contract."""

    def __init__(self, journey: DemoJourney = run_offline_shell_demo) -> None:
        self._journey = journey
        self._default_result: Mapping[str, object] | None = None

    def get(self, path: str) -> JsonObject:
        routes: dict[str, Callable[[Mapping[str, object]], JsonObject]] = {
            "/api/v1/bootstrap": self._bootstrap,
            "/api/v1/session": self._session,
            "/api/v1/materials": self._materials,
            "/api/v1/artifacts": self._artifacts,
            "/api/v1/assessments": self._assessments,
            "/api/v1/evidence": self._evidence,
            "/api/v1/recall/due": self._recall,
            "/api/v1/context/conflicts": self._conflicts,
            "/api/v1/plan": self._plan,
        }
        route = routes.get(path)
        if route is None:
            raise UiRequestError("route not found", status_code=404)
        return route(self._cached_default_result())

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        if path != "/api/v1/session/turns":
            raise UiRequestError(
                "mutation is not available in the public demo", status_code=405
            )
        request_id, expected_sequence, payload = _command(command)
        content = _bounded_content(payload.get("content"))
        result = self._result(content)
        high_water = _sequence(result)
        if expected_sequence != high_water:
            raise UiRequestError("expected sequence is stale", status_code=409)
        session = cast(
            JsonObject,
            {**self._session(result), "persistence": "stateless_public_demo"},
        )
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": "demo_completed",
            "high_water_sequence": high_water,
            "result": session,
        }

    def _result(self, learner_entry: str) -> Mapping[str, object]:
        result = self._journey(learner_entry)
        if not isinstance(result, Mapping):
            raise TypeError("demo journey must return a mapping")
        return result

    def _cached_default_result(self) -> Mapping[str, object]:
        if self._default_result is None:
            self._default_result = dict(self._result(DEFAULT_DEMO_ENTRY))
        return self._default_result

    @staticmethod
    def _bootstrap(result: Mapping[str, object]) -> JsonObject:
        return {
            "schema_version": 1,
            "mode": "public_demo",
            "course": {"id": DEMO_COURSE_ID, "title": DEMO_COURSE_TITLE},
            "session": {"id": DEMO_SESSION_ID, "status": "active"},
            "high_water_sequence": _sequence(result),
            "shell_status": str(result.get("status", "degraded")),
            "features": {
                "tutor": True,
                "artifacts": False,
                "assessments": False,
                "evidence": True,
                "recall": False,
                "context_resolution": False,
                "exam_plan": False,
            },
            "counts": {
                "pending_proposals": 0,
                "due_reviews": 0,
                "context_conflicts": 0,
            },
        }

    @staticmethod
    def _session(result: Mapping[str, object]) -> JsonObject:
        return {
            "schema_version": 1,
            "status": str(result.get("status", "degraded")),
            "course_id": DEMO_COURSE_ID,
            "session_id": DEMO_SESSION_ID,
            "high_water_sequence": _sequence(result),
            "learner_entry": str(result.get("learner_entry", "")),
            "timeline": _objects(result.get("status_trace")),
            "continuation": None,
            "mode": "public_demo",
        }

    @staticmethod
    def _materials(result: Mapping[str, object]) -> JsonObject:
        material = _mapping(result.get("material"))
        evidence = _strings(material.get("evidence"))
        return {
            "schema_version": 1,
            "status": "ready",
            "high_water_sequence": _sequence(result),
            "items": (
                {
                    "source_id": "demo-heart-valves",
                    "title": str(material.get("title", "Sanitized demo material")),
                    "filename": str(material.get("fixture", "unavailable")),
                    "checksum_sha256": str(material.get("checksum_sha256", "")),
                    "byte_size": _integer(material.get("byte_size"), default=0),
                    "evidence": evidence,
                    "trust": "bundled_sanitized_fixture",
                },
            ),
        }

    @staticmethod
    def _artifacts(result: Mapping[str, object]) -> JsonObject:
        return _unavailable(result, "Artifact proposals require repository-backed mode.")

    @staticmethod
    def _assessments(result: Mapping[str, object]) -> JsonObject:
        return _unavailable(result, "Assessments require repository-backed mode.")

    @staticmethod
    def _evidence(result: Mapping[str, object]) -> JsonObject:
        context = _mapping(result.get("context_state"))
        focus = context.get("selected_focus")
        estimates: tuple[JsonObject, ...] = ()
        if isinstance(focus, str) and focus:
            estimates = (
                {
                    "dimension": "demo_context",
                    "key": "selected_focus",
                    "label": "Selected focus",
                    "value": focus,
                    "disposition": "observed",
                },
            )
        return {
            "schema_version": 1,
            "status": "ready",
            "through_sequence": _sequence(result),
            "estimates": estimates,
            "message": "Demo context only; no mastery percentage is inferred.",
        }

    @staticmethod
    def _recall(result: Mapping[str, object]) -> JsonObject:
        due = _mapping(result.get("due_review"))
        return {
            "schema_version": 1,
            "status": "unavailable",
            "high_water_sequence": _sequence(result),
            "items": (),
            "message": str(due.get("message", "Recall is not configured.")),
        }

    @staticmethod
    def _conflicts(result: Mapping[str, object]) -> JsonObject:
        return {
            "schema_version": 1,
            "status": "ready",
            "high_water_sequence": _sequence(result),
            "items": (),
            "message": "No learner-context conflict exists in the sanitized demo.",
        }

    @staticmethod
    def _plan(result: Mapping[str, object]) -> JsonObject:
        return _unavailable(
            result,
            "Exam planning has no canonical owner yet; nothing is stored in the browser.",
        )


def _unavailable(result: Mapping[str, object], message: str) -> JsonObject:
    return {
        "schema_version": 1,
        "status": "unavailable",
        "high_water_sequence": _sequence(result),
        "items": (),
        "message": message,
    }


def _command(command: Mapping[str, object]) -> tuple[str, int, Mapping[str, object]]:
    if not isinstance(command, Mapping) or set(command) != {
        "schema_version",
        "request_id",
        "expected_sequence",
        "payload",
    }:
        raise UiRequestError("command shape is invalid")
    if command.get("schema_version") != 1 or isinstance(command.get("schema_version"), bool):
        raise UiRequestError("schema version is unsupported")
    request_id = command.get("request_id")
    expected = command.get("expected_sequence")
    payload = command.get("payload")
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or len(request_id) > 200
        or not _is_utf8(request_id)
    ):
        raise UiRequestError("request_id is invalid")
    if type(expected) is not int or expected < 0:
        raise UiRequestError("expected_sequence is invalid")
    if not isinstance(payload, Mapping) or set(payload) != {"content"}:
        raise UiRequestError("command payload is invalid")
    return request_id, expected, payload


def _bounded_content(value: object) -> str:
    if not isinstance(value, str):
        raise UiRequestError("content is invalid")
    content = value.strip()
    if (
        not content
        or len(content) > MAX_LEARNER_ENTRY_CHARS
        or not _is_utf8(content)
    ):
        raise UiRequestError("content is invalid")
    return content


def _is_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _sequence(result: Mapping[str, object]) -> int:
    value = result.get("evidence_sequence", result.get("evidence_refresh_sequence"))
    return value if type(value) is int and value >= 0 else 0


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _objects(value: object) -> tuple[JsonObject, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(
        cast(JsonObject, dict(item))
        for item in value
        if isinstance(item, Mapping)
    )


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _integer(value: object, *, default: int) -> int:
    return value if type(value) is int else default


__all__ = [
    "DEFAULT_DEMO_ENTRY",
    "DemoJourney",
    "DemoUiApplication",
    "UiRequestError",
]
