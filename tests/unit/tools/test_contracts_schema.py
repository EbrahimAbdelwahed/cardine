from __future__ import annotations

import math

import pytest

from study_agent.domain._validation import JsonObject
from study_agent.tools import (
    IdempotencyMode,
    SchemaValidationError,
    StudyEvent,
    StudyEventKind,
    ToolEffect,
    ToolError,
    ToolErrorCode,
    ToolManifest,
    ToolResult,
    validate_json,
    validate_schema_definition,
)


def object_schema() -> JsonObject:
    return {
        "type": "object",
        "properties": {
            "count": {"type": "integer", "minimum": 1, "maximum": 3},
            "label": {"type": "string", "minLength": 1},
            "values": {"type": "array", "items": {"type": "number"}},
        },
        "required": ("count", "label", "values"),
        "additionalProperties": False,
    }


def test_strict_schema_rejects_unknown_bool_as_int_and_nonfinite() -> None:
    schema = object_schema()
    validate_json({"count": 1, "label": "ok", "values": (0.5,)}, schema)
    invalid_values: tuple[JsonObject, ...] = (
        {"count": True, "label": "ok", "values": ()},
        {"count": 1, "label": " ok", "values": ()},
        {"count": 1, "label": "ok", "values": (), "authority": "forged"},
        {"count": 1, "label": "ok", "values": (math.inf,)},
    )
    for invalid in invalid_values:
        with pytest.raises(SchemaValidationError):
            validate_json(invalid, schema)


def test_schema_definition_requires_closed_objects_and_known_keywords() -> None:
    with pytest.raises(ValueError, match="additionalProperties"):
        validate_schema_definition({"type": "object", "properties": {}, "required": ()})
    with pytest.raises(ValueError, match="unsupported schema keywords"):
        validate_schema_definition({"type": "string", "pattern": ".*"})


def test_string_schema_enforces_a_maximum_length() -> None:
    schema: JsonObject = {"type": "string", "minLength": 1, "maxLength": 3}
    validate_json("abc", schema)
    with pytest.raises(SchemaValidationError, match="too long"):
        validate_json("abcd", schema)


def test_manifest_fingerprint_is_canonical_and_result_is_strict_xor() -> None:
    schema: JsonObject = {
        "type": "object",
        "properties": {},
        "required": (),
        "additionalProperties": False,
    }
    manifest = ToolManifest(
        "course.get",
        "1.0.0",
        schema,
        schema,
        ToolEffect.READ_ONLY,
        ("study:read",),
        (),
        tuple(ToolErrorCode),
        IdempotencyMode.NOT_APPLICABLE,
    )
    assert manifest.fingerprint == manifest.fingerprint
    assert len(manifest.fingerprint) == 64
    with pytest.raises(ValueError, match="exactly one"):
        ToolResult()
    with pytest.raises(ValueError, match="exactly one"):
        ToolResult(value={}, error=ToolError(ToolErrorCode.CONFLICT, "conflict"))


def test_study_events_are_closed_ephemeral_values_not_domain_events() -> None:
    event = StudyEvent(
        StudyEventKind.GROUNDING_ACCEPTED,
        {"course_id": "course-1", "session_id": "session-1", "run_id": "run-1"},
    )
    assert event.to_json() == {
        "kind": "grounding.accepted",
        "data": {"course_id": "course-1", "session_id": "session-1", "run_id": "run-1"},
    }
    with pytest.raises(TypeError):
        event.data["run_id"] = "changed"  # type: ignore[index]
    with pytest.raises(ValueError, match="invalid fields"):
        StudyEvent(StudyEventKind.GROUNDING_ACCEPTED, {"run_id": "run-1"})


def test_tool_error_retryability_is_fail_closed() -> None:
    with pytest.raises(ValueError, match="only retryable_conflict"):
        ToolError(ToolErrorCode.CONFLICT, "conflict", retryable=True)
    retry = ToolError(ToolErrorCode.RETRYABLE_CONFLICT, "retry", retryable=True)
    assert retry.to_json()["details"] == {}
