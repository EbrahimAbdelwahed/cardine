from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast

import pytest

from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    PrincipalKind,
    SessionId,
    TutorPresentationId,
    TutorPresentationKind,
    TutorPresentationRecord,
    session_turn_event_id_for,
    tutor_presentation_id_for,
)
from study_agent.domain._validation import JsonObject, JsonValue
from cardine.hosts.contracts import TutorPresentationReceipt
from study_agent.sessions import (
    SESSION_STARTED,
    SESSION_TUTOR_PRESENTATION_RECORDED,
    decode_tutor_presentation_recorded,
    register_session_events,
    session_started_payload,
    tutor_presentation_command_fingerprint,
    tutor_presentation_recorded_payload,
)
from study_agent.state import EventRegistry, PayloadValidationError, Projection, apply_event

NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
COURSE = CourseId("events-course")
SESSION = SessionId("events-session")
SHA_A = "a" * 64
SHA_B = "b" * 64


def _record(*, sequence: int = 2, host_turn_id: str = "host-turn-1") -> TutorPresentationRecord:
    observed = sequence - 1
    key = "presentation-key"
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
        None,
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
        id=tutor_presentation_id_for(COURSE, SESSION, host_turn_id, kind.value),
        session_id=SESSION,
        occurred_at=NOW + timedelta(seconds=sequence),
        kind=kind,
        content=content,
        in_reply_to_interaction_id=None,
        host_turn_id=host_turn_id,
        observed_host_context_sequence=observed,
        host_context_fingerprint=SHA_A,
        decision_fingerprint=SHA_B,
        receipt_fingerprint=receipt.fingerprint,
        continuation_fingerprint=None,
        capability_identity=None,
        response_schema=None,
        idempotency_key=key,
        command_fingerprint=command,
        event_id=session_turn_event_id_for(
            COURSE, SESSION, key, SESSION_TUTOR_PRESENTATION_RECORDED
        ),
        course_sequence=sequence,
    )


def _event(
    sequence: int,
    event_type: str,
    payload: JsonObject,
    *,
    actor: PrincipalKind = PrincipalKind.SERVICE,
    session_id: SessionId = SESSION,
) -> DomainEvent:
    return DomainEvent(
        EventId(f"event-{sequence}"),
        COURSE,
        sequence,
        event_type,
        1,
        Actor(actor, "test-service"),
        NOW + timedelta(seconds=sequence),
        CorrelationId("presentation-events"),
        payload,
        session_id,
    )


def _registry() -> EventRegistry:
    registry = EventRegistry()
    register_session_events(registry)
    return registry


def test_tutor_presentation_codec_binds_id_command_and_event_identity() -> None:
    record = _record()
    event = DomainEvent(
        record.event_id,
        COURSE,
        record.course_sequence,
        SESSION_TUTOR_PRESENTATION_RECORDED,
        1,
        Actor(PrincipalKind.SERVICE, "test-service"),
        record.occurred_at,
        CorrelationId("presentation-events"),
        tutor_presentation_recorded_payload(record),
        SESSION,
    )
    assert decode_tutor_presentation_recorded(event).record == record

    tampered: dict[str, JsonValue] = dict(tutor_presentation_recorded_payload(record))
    tampered["host_turn_id"] = "other-host-turn"
    with pytest.raises(ValueError, match="receipt fingerprint"):
        decode_tutor_presentation_recorded(
            DomainEvent(
                record.event_id,
                COURSE,
                2,
                SESSION_TUTOR_PRESENTATION_RECORDED,
                1,
                Actor(PrincipalKind.SERVICE, "test-service"),
                record.occurred_at,
                CorrelationId("presentation-events"),
                cast(JsonObject, tampered),
                SESSION,
            )
        )

    with pytest.raises(ValueError, match="service authority"):
        decode_tutor_presentation_recorded(
            _event(
                2,
                SESSION_TUTOR_PRESENTATION_RECORDED,
                tutor_presentation_recorded_payload(record),
                actor=PrincipalKind.HUMAN,
            )
        )


@pytest.mark.parametrize(
    ("field", "tampered"),
    (
        ("host_turn_id", "host-turn-tampered"),
        ("kind", TutorPresentationKind.LEARNER_QUESTION.value),
        ("content", "Tampered tutor content."),
        ("observed_host_context_sequence", 0),
        ("host_context_fingerprint", "e" * 64),
        ("decision_fingerprint", "f" * 64),
    ),
)
def test_tutor_presentation_codec_binds_every_direct_receipt_field(
    field: str,
    tampered: JsonValue,
) -> None:
    record = _record()
    payload = dict(tutor_presentation_recorded_payload(record))
    payload[field] = tampered

    with pytest.raises(ValueError, match="receipt fingerprint"):
        decode_tutor_presentation_recorded(
            DomainEvent(
                record.event_id,
                COURSE,
                record.course_sequence,
                SESSION_TUTOR_PRESENTATION_RECORDED,
                1,
                Actor(PrincipalKind.SERVICE, "test-service"),
                record.occurred_at,
                CorrelationId("presentation-events"),
                cast(JsonObject, payload),
                SESSION,
            )
        )


def test_tutor_presentation_codec_rejects_unknown_fields_and_bad_envelope() -> None:
    record = _record()
    payload = dict(tutor_presentation_recorded_payload(record))
    payload["unexpected"] = True
    with pytest.raises(PayloadValidationError, match="fields mismatch"):
        _registry().decode(
            _event(2, SESSION_TUTOR_PRESENTATION_RECORDED, cast(JsonObject, payload))
        )

    with pytest.raises(PayloadValidationError, match=r"event\.session_id"):
        _registry().decode(
            DomainEvent(
                record.event_id,
                COURSE,
                2,
                SESSION_TUTOR_PRESENTATION_RECORDED,
                1,
                Actor(PrincipalKind.SERVICE, "test-service"),
                record.occurred_at,
                CorrelationId("presentation-events"),
                tutor_presentation_recorded_payload(record),
                None,
            )
        )


def test_presentation_event_codec_accepts_continuation_descriptor() -> None:
    base = _record()
    kind = TutorPresentationKind.CONTINUATION_REQUEST
    content = "Confirm?"
    continuation = "d" * 64
    capability = "grounding.ask@1.0.0"
    schema: JsonObject = {"type": "boolean"}
    receipt = TutorPresentationReceipt(
        host_turn_id=base.host_turn_id,
        kind=kind,
        content=content,
        observed_host_context_sequence=base.observed_host_context_sequence,
        host_context_fingerprint=SHA_A,
        decision_fingerprint=SHA_B,
        continuation_fingerprint=continuation,
        capability_identity=capability,
        response_schema=schema,
    )
    command = tutor_presentation_command_fingerprint(
        kind,
        content,
        None,
        base.host_turn_id,
        base.observed_host_context_sequence,
        SHA_A,
        SHA_B,
        receipt.fingerprint,
        continuation,
        capability,
        schema,
    )
    record = TutorPresentationRecord(
        id=TutorPresentationId(
            str(tutor_presentation_id_for(COURSE, SESSION, base.host_turn_id, kind.value))
        ),
        session_id=SESSION,
        occurred_at=base.occurred_at,
        kind=kind,
        content=content,
        in_reply_to_interaction_id=None,
        host_turn_id=base.host_turn_id,
        observed_host_context_sequence=base.observed_host_context_sequence,
        host_context_fingerprint=SHA_A,
        decision_fingerprint=SHA_B,
        receipt_fingerprint=receipt.fingerprint,
        continuation_fingerprint=continuation,
        capability_identity=capability,
        response_schema=schema,
        idempotency_key=base.idempotency_key,
        command_fingerprint=command,
        event_id=base.event_id,
        course_sequence=base.course_sequence,
    )
    event = DomainEvent(
        record.event_id,
        COURSE,
        record.course_sequence,
        SESSION_TUTOR_PRESENTATION_RECORDED,
        1,
        Actor(PrincipalKind.SERVICE, "test-service"),
        record.occurred_at,
        CorrelationId("presentation-events"),
        tutor_presentation_recorded_payload(record),
        SESSION,
    )
    assert decode_tutor_presentation_recorded(event).record == record

    for field, tampered in (
        ("continuation_fingerprint", "e" * 64),
        ("capability_identity", "grounding.ask@2.0.0"),
        ("response_schema", {"type": "string"}),
    ):
        payload = dict(tutor_presentation_recorded_payload(record))
        payload[field] = tampered
        with pytest.raises(ValueError, match="receipt fingerprint"):
            decode_tutor_presentation_recorded(
                DomainEvent(
                    record.event_id,
                    COURSE,
                    record.course_sequence,
                    SESSION_TUTOR_PRESENTATION_RECORDED,
                    1,
                    Actor(PrincipalKind.SERVICE, "test-service"),
                    record.occurred_at,
                    CorrelationId("presentation-events"),
                    cast(JsonObject, payload),
                    SESSION,
                )
            )


def test_old_session_projection_can_replay_without_presentation_state() -> None:
    registry = _registry()
    projection = apply_event(
        Projection(COURSE),
        _event(1, SESSION_STARTED, session_started_payload(SESSION)),
        registry,
    )
    assert "session_tutor_presentations" not in projection.state
