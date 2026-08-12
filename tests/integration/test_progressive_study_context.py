from __future__ import annotations

from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pytest

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.adapters.filesystem.repository_target import (
    LocalRepositoryPaths,
    RepositoryObservationHandle,
    RepositoryTargetInspectionCode,
    inspect_repository_target,
    resolve_explicit_repository_target,
)
from study_agent.adapters.sqlite import observe_local_repository
from study_agent.application import ExportService, ExportStateError
from cardine.cli.repository import LocalRepository
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    SessionId,
    StatementStatus,
    StudyStatementInput,
    StudyStatementKind,
    statement_id_for,
    study_context_event_id_for,
)
from study_agent.lifecycle import RepositoryObservationState
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions import (
    SESSION_INTERACTION_RECORDED,
    SESSION_SCHEMA_VERSION,
    interaction_recorded_payload,
)
from study_agent.study_context import (
    STATEMENT_RECORDED,
    STUDY_CONTEXT_SCHEMA_VERSION,
    RetryableStudyContextConflictError,
    StudyContextCommandError,
    StudyContextConflictError,
)
from study_agent.study_context.events import statement_recorded_payload


class _StaticEvents:
    def __init__(self, events: Sequence[DomainEvent]) -> None:
        self._events = tuple(events)

    def read(
        self, course_id: CourseId, after_sequence: int = 0
    ) -> tuple[DomainEvent, ...]:
        return tuple(
            event
            for event in self._events
            if event.course_id == course_id and event.course_sequence > after_sequence
        )

    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: Sequence[DomainEvent],
    ) -> int:
        raise AssertionError("static export fixture is read-only")


def _context(
    course_id: CourseId,
    *,
    session_id: SessionId | None = None,
    key: str | None = None,
    actor: PrincipalKind = PrincipalKind.SERVICE,
) -> ExecutionContext:
    return ExecutionContext(
        actor,
        "progressive-context-test",
        course_id,
        CorrelationId(f"correlation-{key or 'setup'}"),
        session_id=session_id,
        idempotency_key=key,
    )


def _append_human_origin(
    repository: LocalRepository,
    course_id: CourseId,
    session_id: SessionId,
    interaction_id: InteractionId,
) -> int:
    stream = repository.events.read(course_id)
    sequence = stream[-1].course_sequence
    event = DomainEvent(
        EventId("event-human-study-request"),
        course_id,
        sequence + 1,
        SESSION_INTERACTION_RECORDED,
        SESSION_SCHEMA_VERSION,
        Actor(PrincipalKind.HUMAN, "learner"),
        repository.clock.now(),
        CorrelationId("correlation-human-study-request"),
        interaction_recorded_payload(
            interaction_id,
            InteractionKind.HUMAN,
            "Devo preparare l'esame di anatomia.",
        ),
        session_id,
    )
    return repository.events.append(course_id, sequence, (event,))


def _observation_handle(paths: LocalRepositoryPaths) -> RepositoryObservationHandle:
    target = resolve_explicit_repository_target(paths.root)
    inspection = inspect_repository_target(target, EMPTY_CONFIG)
    assert inspection.code is RepositoryTargetInspectionCode.COMPATIBLE
    assert inspection.observation is not None
    return inspection.observation


def test_progressive_context_lifecycle_replay_export_and_observation(
    tmp_path: Path,
) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    course_id = CourseId("course-adaptive-context")
    session_id = SessionId("session-adaptive-context")
    origin_id = InteractionId("interaction-study-request")

    with LocalRepository(paths, EMPTY_CONFIG) as repository:
        profile = CourseProfile(
            course_id,
            "Anatomy",
            "it",
            exam_date=date(2026, 9, 8),
            assessment_styles=("oral",),
            learning_goals=("Pass anatomy",),
        )
        repository.course_service.create(profile, _context(course_id))
        repository.session_service.start(_context(course_id, session_id=session_id))
        sequence = _append_human_origin(repository, course_id, session_id, origin_id)

        empty = repository.study_context.get(course_id)
        assert empty.sequence == sequence
        assert empty.statements == ()
        assert empty.conflicts == ()

        first = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.OBJECTIVE, "  Pass anatomy  "),
            origin_id,
            _context(course_id, session_id=session_id, key="objective-1"),
            sequence,
        )
        second = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.OBJECTIVE, "Pass anatomy"),
            origin_id,
            _context(course_id, session_id=session_id, key="objective-2"),
            first.sequence,
        )
        objective_ids = tuple(
            item.id
            for item in second.active(StudyStatementKind.OBJECTIVE)
        )
        assert len(objective_ids) == 2
        assert tuple(
            item.value for item in second.active(StudyStatementKind.OBJECTIVE)
        ) == ("Pass anatomy", "Pass anatomy")

        retry = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.OBJECTIVE, "Pass anatomy"),
            origin_id,
            _context(course_id, session_id=session_id, key="objective-1"),
            sequence,
        )
        assert retry.sequence == second.sequence
        assert tuple(item.id for item in retry.active(StudyStatementKind.OBJECTIVE)) == (
            objective_ids
        )
        with pytest.raises(StudyContextConflictError, match="different command"):
            repository.study_context_service.record(
                StudyStatementInput(StudyStatementKind.OBJECTIVE, "Pass histology"),
                origin_id,
                _context(course_id, session_id=session_id, key="objective-1"),
                second.sequence,
            )
        with pytest.raises(TypeError, match="expected_sequence"):
            repository.study_context_service.record(
                StudyStatementInput(StudyStatementKind.OBJECTIVE, "Pass anatomy"),
                origin_id,
                _context(course_id, session_id=session_id, key="objective-1"),
                "stale",  # type: ignore[arg-type]
            )
        before_stale = repository.events.projection_bytes(course_id)
        with pytest.raises(RetryableStudyContextConflictError):
            repository.study_context_service.record(
                StudyStatementInput(StudyStatementKind.TESTING_PREFERENCE, "oral recall"),
                origin_id,
                _context(course_id, session_id=session_id, key="stale-new-command"),
                sequence,
            )
        assert repository.events.projection_bytes(course_id) == before_stale

        deadline_a = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 9, 8)),
            origin_id,
            _context(course_id, session_id=session_id, key="deadline-a"),
            second.sequence,
        )
        deadline_b = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 9, 15)),
            origin_id,
            _context(course_id, session_id=session_id, key="deadline-b"),
            deadline_a.sequence,
        )
        assert len(deadline_b.conflicts) == 1
        selected = deadline_b.active(StudyStatementKind.DEADLINE)[1]
        resolved = repository.study_context_service.resolve(
            StudyStatementKind.DEADLINE,
            selected.id,
            _context(course_id, session_id=session_id, key="resolve-deadline"),
            deadline_b.sequence,
        )
        assert resolved.active(StudyStatementKind.DEADLINE) == (selected,)
        assert resolved.conflicts == ()

        later = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 9, 22)),
            origin_id,
            _context(course_id, session_id=session_id, key="deadline-c"),
            resolved.sequence,
        )
        assert len(later.conflicts) == 1
        retracted = repository.study_context_service.retract(
            selected.id,
            _context(course_id, session_id=session_id, key="retract-winner"),
            later.sequence,
        )
        statuses = {item.value: item.status for item in retracted.statements if item.kind.is_scalar}
        assert statuses == {
            date(2026, 9, 8): StatementStatus.SUPERSEDED,
            date(2026, 9, 15): StatementStatus.RETRACTED,
            date(2026, 9, 22): StatementStatus.ACTIVE,
        }
        assert retracted.conflicts == ()

        with pytest.raises(StudyContextCommandError, match="human or service"):
            repository.study_context_service.record(
                StudyStatementInput(StudyStatementKind.OBJECTIVE, "Model-authored fact"),
                origin_id,
                _context(
                    course_id,
                    session_id=session_id,
                    key="model-write",
                    actor=PrincipalKind.MODEL,
                ),
                retracted.sequence,
            )

        projection = repository.events.projection_bytes(course_id)
        assert repository.events.rebuild_projection(course_id) == projection
        bundle = ExportService(repository.events).assemble(course_id)
        assert bundle.high_water_sequence == retracted.sequence
        assert tuple(item["event_type"] for item in bundle.events[-6:]) == (
            "study_context.statement_recorded",
            "study_context.statement_recorded",
            "study_context.statement_recorded",
            "study_context.conflict_resolved",
            "study_context.statement_recorded",
            "study_context.statement_retracted",
        )

    with _observation_handle(paths) as handle:
        observed = observe_local_repository(handle, EMPTY_CONFIG)

    assert observed.state is RepositoryObservationState.COMPATIBLE
    assert observed.courses[0].high_water_sequence == bundle.high_water_sequence


def test_export_rejects_context_event_with_orphan_origin(tmp_path: Path) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    course_id = CourseId("course-invalid-context-export")
    session_id = SessionId("session-invalid-context-export")
    with LocalRepository(paths, EMPTY_CONFIG) as repository:
        repository.course_service.create(
            CourseProfile(course_id, "Anatomy", "it", learning_goals=("Learn",)),
            _context(course_id),
        )
        repository.session_service.start(_context(course_id, session_id=session_id))
        stream = tuple(repository.events.read(course_id))
        sequence = stream[-1].course_sequence
        event_id = study_context_event_id_for(
            course_id, session_id, "orphan-origin", "record"
        )
        statement = StudyStatementInput(StudyStatementKind.OBJECTIVE, "Learn anatomy")
        malformed = DomainEvent(
            event_id,
            course_id,
            sequence + 1,
            STATEMENT_RECORDED,
            STUDY_CONTEXT_SCHEMA_VERSION,
            Actor(PrincipalKind.SERVICE, "invalid-export-fixture"),
            repository.clock.now(),
            CorrelationId("correlation-invalid-export"),
            statement_recorded_payload(
                statement_id_for(event_id),
                InteractionId("interaction-does-not-exist"),
                statement,
                session_id,
                "orphan-origin",
            ),
            session_id,
        )

        with pytest.raises(ExportStateError, match="contextual events"):
            ExportService(_StaticEvents((*stream, malformed))).assemble(course_id)
