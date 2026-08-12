from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.courses import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    CourseCommandError,
    CourseConflictError,
    CourseService,
    ProjectionCourseView,
    RetryableCourseConflictError,
    course_event_id_for,
    course_profile_manifest,
    register_course_events,
)
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.ports import CourseNotFoundError, EventSequenceConflictError
from study_agent.sessions import ProjectionSessionView, SessionService, register_session_events
from study_agent.state import EventRegistry


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, 8, tzinfo=UTC)


class RacingCourseEventStore:
    def __init__(
        self,
        delegate: SQLiteEventStore,
        winner: CourseProfile | None,
    ) -> None:
        self._delegate = delegate
        self._winner = winner

    def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
        return self._delegate.read(course_id, after_sequence)

    def append(self, course_id, expected_sequence, events):  # type: ignore[no-untyped-def]
        incoming = tuple(events)
        if self._winner is not None:
            requested = incoming[0]
            winner = DomainEvent(
                course_event_id_for(self._winner),
                course_id,
                1,
                COURSE_CREATED,
                COURSE_SCHEMA_VERSION,
                Actor(requested.actor.kind, requested.actor.principal_id),
                requested.occurred_at,
                requested.correlation_id,
                course_profile_manifest(self._winner),
            )
            self._delegate.append(course_id, 0, (winner,))
        raise EventSequenceConflictError(course_id, expected_sequence, expected_sequence + 1)


def context(course_id: CourseId, *, session: bool = False) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "test-service",
        course_id,
        CorrelationId("correlation-1"),
        session_id=SessionId("session-1") if session else None,
    )


def stack(tmp_path: Path):  # type: ignore[no-untyped-def]
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = ProjectionCourseView(events.projection)
    sessions = ProjectionSessionView(events.projection)
    return blobs, events, courses, sessions


def test_create_is_idempotent_conflicts_on_change_and_replays(tmp_path: Path) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-1")
    original = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    service = CourseService(events, Clock(), courses)

    assert service.create(original, context(course_id)) == original
    before = events.projection_bytes(course_id)
    assert service.create(original, context(course_id)) == original
    assert len(events.read(course_id)) == 1
    assert events.projection_bytes(course_id) == before

    changed = CourseProfile(course_id, "Changed", "it", learning_goals=("Learn",))
    with pytest.raises(CourseConflictError):
        service.create(changed, context(course_id))
    assert events.rebuild_projection(course_id) == before


def test_create_rejects_wrong_course_model_and_session_scope(tmp_path: Path) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-1")
    profile = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    service = CourseService(events, Clock(), courses)

    with pytest.raises(CourseCommandError):
        service.create(profile, context(CourseId("other")))
    model = ExecutionContext(
        PrincipalKind.MODEL,
        "model",
        course_id,
        CorrelationId("correlation-model"),
    )
    with pytest.raises(CourseCommandError):
        service.create(profile, model)
    with pytest.raises(CourseCommandError):
        service.create(profile, context(course_id, session=True))
    forged = ExecutionContext(
        "service",  # type: ignore[arg-type]
        "forged-service",
        course_id,
        CorrelationId("correlation-forged"),
    )
    with pytest.raises(CourseCommandError, match="trusted human or service"):
        service.create(profile, forged)
    assert events.read(course_id) == ()


def test_create_reconciles_deterministic_same_profile_race(tmp_path: Path) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-race")
    profile = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    service = CourseService(RacingCourseEventStore(events, profile), Clock(), courses)

    assert service.create(profile, context(course_id)) == profile
    assert next(iter(events.read(course_id))).event_id == course_event_id_for(profile)


def test_create_reports_changed_profile_winner_as_conflict(tmp_path: Path) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-race")
    requested = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    winner = CourseProfile(course_id, "Surgery", "it", learning_goals=("Learn",))
    service = CourseService(RacingCourseEventStore(events, winner), Clock(), courses)

    with pytest.raises(CourseConflictError):
        service.create(requested, context(course_id))
    assert courses.get(course_id) == winner


def test_sequence_race_without_course_is_retryable(tmp_path: Path) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-race")
    profile = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    service = CourseService(RacingCourseEventStore(events, None), Clock(), courses)

    with pytest.raises(RetryableCourseConflictError):
        service.create(profile, context(course_id))
    with pytest.raises(CourseNotFoundError):
        courses.get(course_id)


def test_create_expected_sequence_is_checked_before_idempotent_return(
    tmp_path: Path,
) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-cas")
    profile = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    service = CourseService(events, Clock(), courses)

    assert service.create(profile, context(course_id), expected_sequence=0) == profile
    with pytest.raises(RetryableCourseConflictError, match="expected sequence"):
        service.create(profile, context(course_id), expected_sequence=0)
    assert len(events.read(course_id)) == 1


def test_create_expected_sequence_reports_identical_append_race_as_retryable(
    tmp_path: Path,
) -> None:
    _, events, courses, _ = stack(tmp_path)
    course_id = CourseId("course-cas-race")
    profile = CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",))
    service = CourseService(RacingCourseEventStore(events, profile), Clock(), courses)

    with pytest.raises(RetryableCourseConflictError, match="advanced before creation"):
        service.create(profile, context(course_id), expected_sequence=0)
    assert courses.get(course_id) == profile


def test_orphan_ingestion_and_session_fail_before_writes(tmp_path: Path) -> None:
    blobs, events, courses, sessions = stack(tmp_path)
    course_id = CourseId("missing")
    ingestion = TextIngestionService(
        blobs=blobs, events=events, clock=Clock(), courses=courses
    )
    blob_entries_before = tuple((tmp_path / "blobs").rglob("*"))
    with pytest.raises(CourseNotFoundError):
        ingestion.ingest(
            filename="notes.txt",
            content=b"Never written",
            source_id=SourceId("source-1"),
            title="Notes",
            trust_level=100,
            source_role="primary",
            context=context(course_id),
        )
    assert tuple((tmp_path / "blobs").rglob("*")) == blob_entries_before
    assert events.read(course_id) == ()

    with pytest.raises(CourseNotFoundError):
        SessionService(events, Clock(), sessions, courses).start(
            context(course_id, session=True)
        )
    assert events.read(course_id) == ()


def test_course_source_and_session_mixed_replay_is_identical(tmp_path: Path) -> None:
    blobs, events, courses, sessions = stack(tmp_path)
    course_id = CourseId("course-1")
    CourseService(events, Clock(), courses).create(
        CourseProfile(course_id, "Medicine", "it", learning_goals=("Learn",)),
        context(course_id),
    )
    TextIngestionService(
        blobs=blobs, events=events, clock=Clock(), courses=courses
    ).ingest(
        filename="notes.md",
        content=b"# Heart\n\nThe myocardium contracts.",
        source_id=SourceId("source-1"),
        title="Notes",
        trust_level=100,
        source_role="primary",
        context=context(course_id),
    )
    SessionService(events, Clock(), sessions, courses).start(
        context(course_id, session=True)
    )
    before = events.projection_bytes(course_id)

    assert events.verify_projection(course_id)
    assert events.rebuild_projection(course_id) == before
    assert set(events.projection(course_id).state) >= {"course", "sources", "sessions"}
