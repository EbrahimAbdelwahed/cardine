from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cardine.courses import CourseService, ProjectionCourseView, register_course_events
from cardine.hosts import TutorPresentationReceipt
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    InteractionId,
    PrincipalKind,
    SessionId,
    TutorPresentationKind,
)
from study_agent.sessions import (
    IdempotencyConflictError,
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    ProjectionTutorPresentationView,
    RetryableSessionConflictError,
    SessionCommandError,
    SessionService,
    SessionTurnService,
    register_session_events,
)
from study_agent.state import EventRegistry

COURSE = CourseId("presentation-course")
SESSION = SessionId("presentation-session")
NOW = datetime(2026, 7, 29, 12, tzinfo=UTC)
SHA_A = "a" * 64
SHA_B = "b" * 64


class Clock:
    def now(self) -> datetime:
        return NOW


def _context(
    *,
    session_id: SessionId = SESSION,
    key: str = "presentation-key",
    principal: PrincipalKind = PrincipalKind.SERVICE,
    course_id: CourseId = COURSE,
) -> ExecutionContext:
    return ExecutionContext(
        principal,
        "presentation-test",
        course_id,
        CorrelationId(f"correlation-{key}"),
        session_id=session_id,
        idempotency_key=key,
    )


def _receipt(
    *,
    host_turn_id: str = "host-turn-1",
    observed: int,
    content: str = "A validated tutor message.",
) -> TutorPresentationReceipt:
    return TutorPresentationReceipt(
        host_turn_id,
        TutorPresentationKind.ASSISTANT_MESSAGE,
        content,
        observed,
        SHA_A,
        SHA_B,
    )


def _setup(path: Path) -> tuple[SQLiteEventStore, ProjectionSessionView, SessionTurnService]:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    events = SQLiteEventStore(path, registry)
    CourseService(events, Clock(), ProjectionCourseView(events.projection)).create(
        CourseProfile(COURSE, "Presentation course", "en", learning_goals=("test",)),
        ExecutionContext(
            PrincipalKind.SERVICE,
            "course-fixture",
            COURSE,
            CorrelationId("course-created"),
        ),
    )
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, ProjectionCourseView(events.projection)).start(
        _context()
    )
    turns = SessionTurnService(
        events,
        Clock(),
        sessions,
        ProjectionAssistantTurnView(events.projection),
        ProjectionTutorPresentationView(events.projection),
    )
    return events, sessions, turns


def test_presentation_retry_is_idempotent_and_changed_receipt_conflicts(tmp_path: Path) -> None:
    events, sessions, turns = _setup(tmp_path / "events.sqlite3")
    observed = events.read(COURSE)[-1].course_sequence
    receipt = _receipt(observed=observed)

    first = turns.record_tutor_presentation(
        context=_context(), receipt=receipt, expected_sequence=observed
    )
    retry = turns.record_tutor_presentation(
        context=_context(), receipt=receipt, expected_sequence=observed
    )

    assert retry == first
    assert sessions.get_session(COURSE, SESSION).status.value == "active"
    assert len(events.read(COURSE)) == observed + 1

    with pytest.raises(IdempotencyConflictError, match="different content"):
        turns.record_tutor_presentation(
            context=_context(),
            receipt=_receipt(observed=observed, content="Changed message."),
            expected_sequence=observed,
        )


def test_presentation_requires_cas_against_receipt_observed_sequence(tmp_path: Path) -> None:
    events, _, turns = _setup(tmp_path / "events.sqlite3")
    observed = events.read(COURSE)[-1].course_sequence
    receipt = _receipt(observed=observed)

    lifecycle = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), lifecycle, ProjectionCourseView(events.projection)).record_note(
        _context(key="advance-stream"), "Advance the stream."
    )

    with pytest.raises(RetryableSessionConflictError, match="stream advanced"):
        turns.record_tutor_presentation(context=_context(), receipt=receipt)


def test_presentation_rejects_untrusted_authority_and_invalid_reply_target(tmp_path: Path) -> None:
    events, _, turns = _setup(tmp_path / "events.sqlite3")
    observed = events.read(COURSE)[-1].course_sequence
    receipt = _receipt(observed=observed)

    with pytest.raises(SessionCommandError, match="service authority"):
        turns.record_tutor_presentation(
            context=_context(principal=PrincipalKind.HUMAN), receipt=receipt
        )
    with pytest.raises(SessionCommandError, match="reply target"):
        turns.record_tutor_presentation(
            context=_context(),
            receipt=receipt,
            in_reply_to_interaction_id=InteractionId("missing-interaction"),
        )
    assert ProjectionTutorPresentationView(events.projection).presentations(COURSE, SESSION) == ()


def test_host_turn_identity_cannot_be_reused_by_another_session(tmp_path: Path) -> None:
    events, _, turns = _setup(tmp_path / "events.sqlite3")
    observed = events.read(COURSE)[-1].course_sequence
    receipt = _receipt(observed=observed)
    turns.record_tutor_presentation(context=_context(), receipt=receipt)

    other_session = SessionId("other-presentation-session")
    other_context = _context(session_id=other_session, key="other-session-start")
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, ProjectionCourseView(events.projection)).start(
        other_context
    )
    turns_for_other = SessionTurnService(
        events,
        Clock(),
        sessions,
        ProjectionAssistantTurnView(events.projection),
        ProjectionTutorPresentationView(events.projection),
    )
    current = events.read(COURSE)[-1].course_sequence
    with pytest.raises(IdempotencyConflictError, match="another session"):
        turns_for_other.record_tutor_presentation(
            context=_context(session_id=other_session, key="other-presentation"),
            receipt=_receipt(observed=current),
        )
