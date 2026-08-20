from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

from cardine.application.conversation_history import ConversationHistoryReader
from study_agent.domain import (
    CourseId,
    EventId,
    InteractionId,
    RunId,
    SessionId,
    TutorTimelineEntry,
    TutorTimelineKind,
    TutorTimelineStatus,
)
from tests.contract.tutor_snapshot.test_snapshot_reader_contract import (
    COURSE,
    SESSION,
    _context,
)


class _SnapshotView:
    def __init__(self, snapshot: object) -> None:
        self.snapshot = snapshot

    def get(self, course_id: CourseId, session_id: SessionId) -> object:
        assert (course_id, session_id) == (COURSE, SESSION)
        return self.snapshot


class _PresentationView:
    def presentations(self, course_id: CourseId, session_id: SessionId) -> tuple[object, ...]:
        assert (course_id, session_id) == (COURSE, SESSION)
        return ()


def test_history_searches_canonical_timeline_through_snapshot_high_water(
    tmp_path,
) -> None:
    """The first public tracer for bounded, course/session-owned history search."""
    from cardine.cli.repository import LocalRepository
    from study_agent.adapters.filesystem import initialize_local_repository
    from study_agent.domain import CourseProfile
    from study_agent.repository_config import EMPTY_CONFIG

    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    with LocalRepository(paths, EMPTY_CONFIG) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Anatomy", "en", learning_goals=("Study",)),
            _context(session_id=None),
        )
        repository.session_service.start(_context())
        snapshot = repository.tutor_snapshots.get(COURSE, SESSION)

    timeline = tuple(
        TutorTimelineEntry(
            kind=TutorTimelineKind.LEARNER,
            interaction_id=InteractionId(f"interaction-{index}"),
            occurred_at=datetime(2026, 8, 14, 9, index, tzinfo=UTC),
            content=("old topic" if index == 1 else "unrelated topic"),
            event_id=EventId(f"event-{index}"),
            course_sequence=index + 2,
        )
        for index in range(1, 4)
    )
    snapshot = replace(
        snapshot,
        high_water_sequence=5,
        timeline=timeline,
        notes=(),
    )
    result = ConversationHistoryReader(_SnapshotView(snapshot), _PresentationView()).search(
        COURSE,
        SESSION,
        "old topic",
        limit=8,
    )

    assert result.total_entries == 3
    assert result.match_count == 1
    assert tuple(item.course_sequence for item in result.entries) == (3,)
    assert result.entries[0].role == "learner"
    assert result.entries[0].excerpt == "old topic"
    assert result.through_sequence == 5


def test_history_reads_bounded_learner_and_assistant_pages_in_sequence_order() -> None:
    from study_agent.domain import (
        SessionStatus,
        StudyStatementKind,
        TutorContextField,
        TutorContextState,
        TutorSnapshotV1,
    )

    timeline = tuple(
        TutorTimelineEntry(
            kind=TutorTimelineKind.ASSISTANT if sequence % 2 == 0 else TutorTimelineKind.LEARNER,
            interaction_id=InteractionId(f"page-interaction-{sequence}"),
            occurred_at=datetime(2026, 8, 14, 9, tzinfo=UTC),
            content=f"message {sequence}",
            event_id=EventId(f"page-event-{sequence}"),
            course_sequence=sequence,
            run_id=RunId(f"run-{sequence}") if sequence % 2 == 0 else None,
            status=TutorTimelineStatus.COMPLETED if sequence % 2 == 0 else None,
        )
        for sequence in range(2, 32)
    )
    snapshot = TutorSnapshotV1(
        COURSE,
        SESSION,
        32,
        SessionStatus.ACTIVE,
        None,
        (),
        tuple(TutorContextField(kind, TutorContextState.MISSING) for kind in StudyStatementKind),
        (),
        timeline,
        (),
        (),
    )

    reader = ConversationHistoryReader(_SnapshotView(snapshot), _PresentationView())
    first = reader.read(COURSE, SESSION, direction="forward", limit=4, through_sequence=20)
    assert first.total_entries == 19
    assert first.through_sequence == 20
    assert tuple(item.course_sequence for item in first.entries) == (2, 3, 4, 5)
    assert [item.role for item in first.entries] == ["assistant", "learner", "assistant", "learner"]
    assert first.has_more is True
    assert first.next_cursor is not None

    second = reader.read(
        COURSE,
        SESSION,
        direction="forward",
        cursor=first.next_cursor,
        limit=4,
        through_sequence=20,
    )
    assert tuple(item.course_sequence for item in second.entries) == (6, 7, 8, 9)
    assert tuple(item.course_sequence for item in second.entries) == tuple(
        sorted(item.course_sequence for item in second.entries)
    )


def test_context_exposes_omitted_older_conversation_entries() -> None:
    from cardine.hosts.context import TutorHostContextAssembler
    from study_agent.assessments import LearnerEvidenceSnapshot
    from study_agent.domain import (
        SessionStatus,
        StudyStatementKind,
        TutorContextField,
        TutorContextState,
        TutorSnapshotV1,
    )

    timeline = tuple(
        TutorTimelineEntry(
            TutorTimelineKind.LEARNER,
            InteractionId(f"metadata-interaction-{sequence}"),
            datetime(2026, 8, 14, 9, tzinfo=UTC),
            f"topic {sequence}",
            EventId(f"metadata-event-{sequence}"),
            sequence,
        )
        for sequence in range(2, 32)
    )
    snapshot = TutorSnapshotV1(
        COURSE,
        SESSION,
        32,
        SessionStatus.ACTIVE,
        None,
        (),
        tuple(TutorContextField(kind, TutorContextState.MISSING) for kind in StudyStatementKind),
        (),
        timeline,
        (),
        (),
    )
    evidence = LearnerEvidenceSnapshot(COURSE, 32, ())

    class _Evidence:
        def get(self, course_id: CourseId) -> LearnerEvidenceSnapshot:
            assert course_id == COURSE
            return evidence

    class _Capabilities:
        def discover(self) -> tuple[object, ...]:
            return ()

    context = TutorHostContextAssembler(
        _SnapshotView(snapshot), _Evidence(), _Capabilities()
    ).assemble(
        COURSE,
        SESSION,
    )
    assert context.tutor_snapshot["conversation_window"] == {
        "total_entries": 30,
        "included_entries": 24,
        "omitted_entries": 6,
        "through_sequence": 32,
    }
