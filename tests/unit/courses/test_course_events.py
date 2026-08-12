from __future__ import annotations

from datetime import UTC, date, datetime

import pytest

from cardine.courses import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    course_command_fingerprint,
    course_event_id_for,
    course_profile_manifest,
    decode_course_created,
    decode_course_profile,
)
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    PrincipalKind,
    SourcePolicy,
    TerminologyEntry,
    TerminologyPolicy,
)


def profile() -> CourseProfile:
    return CourseProfile(
        CourseId("course-medicine"),
        "Medicine II",
        "it",
        date(2026, 9, 15),
        ("oral", "written"),
        ("Explain mechanisms", "Resolve cases"),
        SourcePolicy(("primary", "supplement"), 70),
        TerminologyPolicy((TerminologyEntry("heart", "cuore"),)),
    )


def event(*, actor: PrincipalKind = PrincipalKind.HUMAN) -> DomainEvent:
    value = profile()
    return DomainEvent(
        course_event_id_for(value),
        value.id,
        1,
        COURSE_CREATED,
        COURSE_SCHEMA_VERSION,
        Actor(actor, "owner"),
        datetime(2026, 7, 12, 8, tzinfo=UTC),
        CorrelationId("correlation-create"),
        course_profile_manifest(value),
    )


def test_complete_profile_manifest_round_trips_and_identity_is_deterministic() -> None:
    value = profile()
    decoded = decode_course_created(event())

    assert decoded.profile == value
    assert course_profile_manifest(decoded.profile) == course_profile_manifest(value)
    assert len(course_command_fingerprint(value)) == 64
    assert course_event_id_for(value) == course_event_id_for(value)


@pytest.mark.parametrize("exam_date", [None, date(2026, 9, 15)])
def test_every_accepted_exam_date_manifest_round_trips(exam_date: date | None) -> None:
    value = CourseProfile(
        CourseId("course-date"),
        "Medicine II",
        "it",
        exam_date,
        learning_goals=("Explain mechanisms",),
    )

    assert decode_course_profile(course_profile_manifest(value)) == value


def test_course_profile_rejects_datetime_as_exam_date() -> None:
    with pytest.raises(TypeError, match="date or None"):
        CourseProfile(
            CourseId("course-datetime"),
            "Medicine II",
            "it",
            datetime(2026, 9, 15, tzinfo=UTC),
            learning_goals=("Explain mechanisms",),
        )


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: {**payload, "unknown": True},
        lambda payload: {key: value for key, value in payload.items() if key != "language"},
        lambda payload: {**payload, "id": "course-other"},
    ],
)
def test_payload_and_envelope_are_strict(mutation) -> None:  # type: ignore[no-untyped-def]
    original = event()
    bad = DomainEvent(
        original.event_id,
        original.course_id,
        original.course_sequence,
        original.event_type,
        original.schema_version,
        original.actor,
        original.occurred_at,
        original.correlation_id,
        mutation(dict(original.payload)),
    )

    with pytest.raises(ValueError):
        decode_course_created(bad)


def test_identity_actor_and_scope_are_strict() -> None:
    original = event(actor=PrincipalKind.MODEL)
    with pytest.raises(ValueError, match="trusted human or service"):
        decode_course_created(original)

    trusted = event()
    wrong_id = DomainEvent(
        EventId("event-wrong"),
        trusted.course_id,
        1,
        trusted.event_type,
        trusted.schema_version,
        trusted.actor,
        trusted.occurred_at,
        trusted.correlation_id,
        trusted.payload,
    )
    with pytest.raises(ValueError, match="event id"):
        decode_course_created(wrong_id)


def test_actor_kind_must_be_a_real_principal_kind() -> None:
    trusted = event()
    forged = DomainEvent(
        trusted.event_id,
        trusted.course_id,
        trusted.course_sequence,
        trusted.event_type,
        trusted.schema_version,
        Actor("human", "owner"),  # type: ignore[arg-type]
        trusted.occurred_at,
        trusted.correlation_id,
        trusted.payload,
    )

    with pytest.raises(ValueError, match="trusted human or service"):
        decode_course_created(forged)
