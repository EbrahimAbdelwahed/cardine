from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta

import pytest

from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    InteractionId,
    PrincipalKind,
    SessionId,
    TutorPresentationKind,
    TutorPresentationRecord,
    session_turn_event_id_for,
    tutor_presentation_id_for,
)
from study_agent.hosts.contracts import TutorPresentationReceipt
from study_agent.sessions import (
    SESSION_STARTED,
    SESSION_TUTOR_PRESENTATION_RECORDED,
    ProjectionTutorPresentationView,
    register_session_events,
    session_started_payload,
    tutor_presentation_command_fingerprint,
    tutor_presentation_recorded_payload,
)
from study_agent.state import EventRegistry, Projection, apply_event

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
COURSE = CourseId("projection-course")
SESSION = SessionId("projection-session")
SHA_A = "a" * 64
SHA_B = "b" * 64


def _record(
    *,
    sequence: int = 2,
    observed: int | None = None,
    host_turn_id: str = "host-turn-1",
    key: str = "presentation-key",
    reply: InteractionId | None = None,
) -> TutorPresentationRecord:
    observed = sequence - 1 if observed is None else observed
    kind = TutorPresentationKind.ASSISTANT_MESSAGE
    content = "A validated tutor message."
    receipt = TutorPresentationReceipt(
        host_turn_id=host_turn_id,
        kind=kind,
        content=content,
        observed_host_context_sequence=observed,
        host_context_fingerprint=SHA_A,
        decision_fingerprint=SHA_B,
    )
    command = tutor_presentation_command_fingerprint(
        kind,
        content,
        reply,
        host_turn_id,
        observed,
        SHA_A,
        SHA_B,
        receipt.fingerprint,
        None,
        None,
        None,
    )
    return TutorPresentationRecord(
        tutor_presentation_id_for(COURSE, SESSION, host_turn_id, kind.value),
        SESSION,
        NOW + timedelta(seconds=sequence),
        kind,
        content,
        reply,
        host_turn_id,
        observed,
        SHA_A,
        SHA_B,
        receipt.fingerprint,
        None,
        None,
        None,
        key,
        command,
        session_turn_event_id_for(COURSE, SESSION, key, SESSION_TUTOR_PRESENTATION_RECORDED),
        sequence,
    )


def _event(record: TutorPresentationRecord) -> DomainEvent:
    return DomainEvent(
        record.event_id,
        COURSE,
        record.course_sequence,
        SESSION_TUTOR_PRESENTATION_RECORDED,
        1,
        Actor(PrincipalKind.SERVICE, "projection-test"),
        record.occurred_at,
        CorrelationId("projection-correlation"),
        tutor_presentation_recorded_payload(record),
        SESSION,
    )


def _started() -> tuple[Projection, EventRegistry]:
    registry = EventRegistry()
    register_session_events(registry)
    projection = apply_event(
        Projection(COURSE),
        DomainEvent(
            EventId("event-1"),
            COURSE,
            1,
            SESSION_STARTED,
            1,
            Actor(PrincipalKind.SERVICE, "projection-test"),
            NOW + timedelta(seconds=1),
            CorrelationId("projection-correlation"),
            session_started_payload(SESSION),
            SESSION,
        ),
        registry,
    )
    return projection, registry


def test_projection_materializes_presentation_and_view_orders_by_course_sequence() -> None:
    projection, registry = _started()
    record = _record()
    projection = apply_event(projection, _event(record), registry)

    view = ProjectionTutorPresentationView(lambda course_id: projection)
    assert view.presentations(COURSE, SESSION) == (record,)
    raw = projection.state["session_tutor_presentations"]
    assert isinstance(raw, Mapping)
    encoded = raw[str(record.id)]
    assert isinstance(encoded, Mapping)
    assert encoded["content"] == record.content


def test_projection_rejects_orphan_reply_and_sequence_mismatch() -> None:
    projection, registry = _started()
    orphan = _record(reply=InteractionId("missing-interaction"))
    with pytest.raises(ValueError, match="reply target"):
        apply_event(projection, _event(orphan), registry)

    invalid_sequence = _record(observed=0)
    invalid_event = _event(invalid_sequence)
    with pytest.raises(ValueError, match="observed host sequence"):
        apply_event(projection, invalid_event, registry)


def test_projection_rejects_duplicate_presentation_identity_and_idempotency_key() -> None:
    projection, registry = _started()
    first = _record()
    projection = apply_event(projection, _event(first), registry)

    duplicate = _record(sequence=3)
    with pytest.raises(ValueError, match="id already exists"):
        apply_event(projection, _event(duplicate), registry)

    second = _record(sequence=3, host_turn_id="host-turn-2")
    with pytest.raises(ValueError, match="idempotency key"):
        apply_event(projection, _event(second), registry)


def test_old_projection_state_has_empty_presentation_view() -> None:
    projection, _ = _started()
    assert "session_tutor_presentations" not in projection.state
    view = ProjectionTutorPresentationView(lambda course_id: projection)
    assert view.presentations(COURSE, SESSION) == ()
