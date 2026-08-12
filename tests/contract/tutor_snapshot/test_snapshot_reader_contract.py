from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.cli.repository import LocalRepository
from cardine.courses import CourseService, ProjectionCourseView, register_course_events
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    StudyStatementInput,
    StudyStatementKind,
    TutorContextState,
    TutorTimelineKind,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions import (
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    SessionService,
    SessionTurnService,
    register_session_events,
)
from study_agent.state import EventRegistry, canonical_json_bytes
from study_agent.study_context import (
    ProjectionStudyContextView,
    StudyContextService,
    register_study_context_events,
)
from study_agent.tutor_snapshot import TutorSnapshotReader

COURSE = CourseId("course-tutor-snapshot")
SESSION = SessionId("session-tutor-snapshot")
NOW = datetime(2026, 7, 15, 9, tzinfo=UTC)


class Clock:
    def now(self) -> datetime:
        return NOW


class CountingEventStore:
    def __init__(
        self, inner: SQLiteEventStore, after_first_capture: Callable[[], None]
    ) -> None:
        self.inner = inner
        self.after_first_capture = after_first_capture
        self.read_count = 0

    def read(
        self, course_id: CourseId, after_sequence: int = 0
    ) -> Sequence[DomainEvent]:
        self.read_count += 1
        captured = self.inner.read(course_id, after_sequence)
        if self.read_count == 1:
            self.after_first_capture()
        return captured

    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: Sequence[DomainEvent],
    ) -> int:
        return self.inner.append(course_id, expected_sequence, events)


def _context(
    key: str | None = None, *, session_id: SessionId | None = SESSION
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "snapshot-contract",
        COURSE,
        CorrelationId(f"correlation-{key or 'setup'}"),
        session_id=session_id,
        idempotency_key=key,
    )


def test_snapshot_uses_one_capture_and_reports_evidence_without_policy(
    tmp_path: Path,
) -> None:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    register_study_context_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = ProjectionCourseView(events.projection)
    CourseService(events, Clock(), courses).create(
        CourseProfile(
            COURSE,
            "Anatomy",
            "en",
            learning_goals=("Configured objective",),
        ),
        _context(session_id=None),
    )
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, courses).start(_context())
    turns = ProjectionAssistantTurnView(events.projection)
    turn_service = SessionTurnService(events, Clock(), sessions, turns)
    learner = turn_service.record_learner_turn(
        "I need to pass the oral exam.", _context("learner"), 2
    )
    context_view = ProjectionStudyContextView(events.projection)
    StudyContextService(events, Clock(), context_view, courses, sessions).record(
        StudyStatementInput(StudyStatementKind.OBJECTIVE, "Learner objective"),
        learner.id,
        _context("objective"),
        3,
    )

    def append_racing_turn() -> None:
        turn_service.record_learner_turn(
            "This arrived after the capture.", _context("racing-learner"), 4
        )

    counted = CountingEventStore(events, append_racing_turn)
    reader = TutorSnapshotReader(counted, registry)
    first = reader.get(COURSE, SESSION)

    assert counted.read_count == 1
    assert first.high_water_sequence == 4
    assert events.read(COURSE)[-1].course_sequence == 5
    assert first.timeline[0].kind is TutorTimelineKind.LEARNER
    assert first.timeline[0].course_sequence == 3
    assert first.learner_context[0].state is TutorContextState.KNOWN
    assert tuple(item.state for item in first.learner_context[1:]) == (
        TutorContextState.MISSING,
        TutorContextState.MISSING,
        TutorContextState.MISSING,
        TutorContextState.MISSING,
    )
    assert first.divergences[0].learner_statement_ids

    second = reader.get(COURSE, SESSION)
    assert counted.read_count == 2
    assert second.high_water_sequence == 5
    assert tuple(item.course_sequence for item in second.timeline) == (3, 5)
    third = reader.get(COURSE, SESSION)
    assert counted.read_count == 3
    assert canonical_json_bytes(second.to_json()) == canonical_json_bytes(third.to_json())
    encoded = canonical_json_bytes(first.to_json())
    for forbidden in (b"next_action", b"capabilities", b"provider", b"mastery"):
        assert forbidden not in encoded


def test_local_repository_exposes_public_snapshot_reader(tmp_path: Path) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    with LocalRepository(paths, EMPTY_CONFIG) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Anatomy", "en", learning_goals=("Learn",)),
            _context(session_id=None),
        )
        repository.session_service.start(_context())

        snapshot = repository.tutor_snapshots.get(COURSE, SESSION)

    assert snapshot.high_water_sequence == 2
    assert snapshot.timeline == ()
    assert len(snapshot.learner_context) == 5


def test_snapshot_reader_fails_closed_on_corrupt_current_material(tmp_path: Path) -> None:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)

    def corrupt_material(
        state: JsonObject, _: DomainEvent, payload: JsonObject
    ) -> Mapping[str, JsonValue]:
        assert payload == {}
        return {
            **state,
            "sources": {
                "source-corrupt": {
                    "current_revision_id": "revision-missing",
                    "revisions": {},
                }
            },
            "chunks": {},
        }

    registry.register("test.corrupt_material", 1, lambda payload: payload, corrupt_material)
    events = SQLiteEventStore(tmp_path / "corrupt.sqlite3", registry)
    courses = ProjectionCourseView(events.projection)
    CourseService(events, Clock(), courses).create(
        CourseProfile(COURSE, "Anatomy", "en", learning_goals=("Learn",)),
        _context(session_id=None),
    )
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, courses).start(_context())
    events.append(
        COURSE,
        2,
        (
            DomainEvent(
                EventId("event-corrupt-material"),
                COURSE,
                3,
                "test.corrupt_material",
                1,
                Actor(PrincipalKind.SERVICE, "snapshot-contract"),
                NOW,
                CorrelationId("correlation-corrupt-material"),
                {},
            ),
        ),
    )

    with pytest.raises(ValueError, match="source projection fields are corrupt"):
        TutorSnapshotReader(events, registry).get(COURSE, SESSION)


def test_snapshot_reader_rejects_timeline_projection_mismatch(tmp_path: Path) -> None:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)

    def corrupt_interaction(
        state: JsonObject, _: DomainEvent, payload: JsonObject
    ) -> Mapping[str, JsonValue]:
        assert payload == {}
        interactions = dict(cast(JsonObject, state["session_interactions"]))
        interaction_id = next(iter(interactions))
        interaction = dict(cast(JsonObject, interactions[interaction_id]))
        interaction["content"] = "projection-only mutation"
        interactions[interaction_id] = interaction
        return {**state, "session_interactions": interactions}

    registry.register(
        "test.corrupt_interaction", 1, lambda payload: payload, corrupt_interaction
    )
    events = SQLiteEventStore(tmp_path / "corrupt-timeline.sqlite3", registry)
    courses = ProjectionCourseView(events.projection)
    CourseService(events, Clock(), courses).create(
        CourseProfile(COURSE, "Anatomy", "en", learning_goals=("Learn",)),
        _context(session_id=None),
    )
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, courses).start(_context())
    turns = ProjectionAssistantTurnView(events.projection)
    SessionTurnService(events, Clock(), sessions, turns).record_learner_turn(
        "Canonical learner content", _context("learner-corrupt"), 2
    )
    events.append(
        COURSE,
        3,
        (
            DomainEvent(
                EventId("event-corrupt-interaction"),
                COURSE,
                4,
                "test.corrupt_interaction",
                1,
                Actor(PrincipalKind.SERVICE, "snapshot-contract"),
                NOW,
                CorrelationId("correlation-corrupt-interaction"),
                {},
            ),
        ),
    )

    with pytest.raises(ValueError, match="captured interaction does not match"):
        TutorSnapshotReader(events, registry).get(COURSE, SESSION)
