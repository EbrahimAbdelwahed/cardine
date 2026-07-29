"""Projection-backed reader for validated tutor presentations."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import datetime

from study_agent.domain import (
    CourseId,
    EventId,
    InteractionId,
    SessionId,
    TutorPresentationId,
    TutorPresentationKind,
    TutorPresentationRecord,
)
from study_agent.domain._validation import JsonValue
from study_agent.ports import SessionNotFoundError
from study_agent.state import Projection

type ProjectionLoader = Callable[[CourseId], Projection]


class ProjectionTutorPresentationView:
    """Read presentation rows without changing the public tutor snapshot."""

    def __init__(self, load_projection: ProjectionLoader) -> None:
        self._load_projection = load_projection

    def presentations(
        self, course_id: CourseId, session_id: SessionId
    ) -> tuple[TutorPresentationRecord, ...]:
        projection = self._load_projection(course_id)
        if projection.course_id != course_id:
            raise ValueError("projection loader returned another course")
        sessions = _mapping(projection.state.get("sessions", {}), "sessions")
        session = sessions.get(str(session_id))
        if session is None:
            raise SessionNotFoundError(course_id, session_id)
        if not isinstance(session, Mapping) or session.get("course_id") != str(course_id):
            raise ValueError("session projection ownership is corrupt")
        raw_presentations = _mapping(
            projection.state.get("session_tutor_presentations", {}),
            "session_tutor_presentations",
        )
        result = tuple(_decode_presentation(key, raw) for key, raw in raw_presentations.items())
        for item in result:
            if item.session_id != session_id:
                continue
            if item.in_reply_to_interaction_id is not None:
                interactions = _mapping(
                    projection.state.get("session_interactions", {}), "session_interactions"
                )
                reply = interactions.get(str(item.in_reply_to_interaction_id))
                if (
                    not isinstance(reply, Mapping)
                    or reply.get("session_id") != str(session_id)
                    or reply.get("kind") != "human"
                ):
                    raise ValueError("presentation reply linkage is corrupt")
        return tuple(
            sorted(
                (item for item in result if item.session_id == session_id),
                key=lambda item: item.course_sequence,
            )
        )


def _decode_presentation(key: object, raw: JsonValue) -> TutorPresentationRecord:
    if not isinstance(key, str) or not isinstance(raw, Mapping):
        raise ValueError("tutor presentation projection entry is corrupt")
    expected = {
        "session_id",
        "presentation_id",
        "kind",
        "content",
        "in_reply_to_interaction_id",
        "host_turn_id",
        "observed_host_context_sequence",
        "host_context_fingerprint",
        "decision_fingerprint",
        "receipt_fingerprint",
        "continuation_fingerprint",
        "capability_identity",
        "response_schema",
        "idempotency_key",
        "command_fingerprint",
        "event_id",
        "course_sequence",
        "occurred_at",
    }
    if set(raw) != expected or raw.get("presentation_id") != key:
        raise ValueError("tutor presentation projection fields are corrupt")
    reply_raw = raw.get("in_reply_to_interaction_id")
    if reply_raw is not None and not isinstance(reply_raw, str):
        raise ValueError("presentation reply linkage is corrupt")
    try:
        kind = TutorPresentationKind(_text(raw, "kind"))
        occurred_at = datetime.fromisoformat(_text(raw, "occurred_at").replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("tutor presentation projection is corrupt") from error
    sequence = raw.get("course_sequence")
    observed = raw.get("observed_host_context_sequence")
    if type(sequence) is not int or isinstance(sequence, bool) or type(observed) is not int:
        raise ValueError("tutor presentation sequence is corrupt")
    continuation = raw.get("continuation_fingerprint")
    capability = raw.get("capability_identity")
    if continuation is not None and not isinstance(continuation, str):
        raise ValueError("continuation fingerprint is corrupt")
    if capability is not None and not isinstance(capability, str):
        raise ValueError("capability identity is corrupt")
    schema = raw.get("response_schema")
    if schema is not None and not isinstance(schema, Mapping):
        raise ValueError("response schema is corrupt")
    return TutorPresentationRecord(
        id=TutorPresentationId(key),
        session_id=SessionId(_text(raw, "session_id")),
        occurred_at=occurred_at,
        kind=kind,
        content=_text(raw, "content"),
        in_reply_to_interaction_id=InteractionId(reply_raw) if isinstance(reply_raw, str) else None,
        host_turn_id=_text(raw, "host_turn_id"),
        observed_host_context_sequence=observed,
        host_context_fingerprint=_text(raw, "host_context_fingerprint"),
        decision_fingerprint=_text(raw, "decision_fingerprint"),
        receipt_fingerprint=_text(raw, "receipt_fingerprint"),
        continuation_fingerprint=continuation,
        capability_identity=capability,
        response_schema=schema,
        idempotency_key=_text(raw, "idempotency_key"),
        command_fingerprint=_text(raw, "command_fingerprint"),
        event_id=EventId(_text(raw, "event_id")),
        course_sequence=sequence,
    )


def _mapping(value: JsonValue | None, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"projection field {name} must be an object")
    return value


def _text(value: Mapping[str, JsonValue], name: str) -> str:
    item = value.get(name)
    if not isinstance(item, str) or not item:
        raise ValueError(f"tutor presentation {name} is corrupt")
    return item
