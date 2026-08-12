from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import cast

import pytest

from cardine.courses import CourseService, ProjectionCourseView, register_course_events
from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    Actor,
    AnswerId,
    AnswerProvenance,
    AnswerStatus,
    AssistantTurnRecord,
    AssistantTurnStatus,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    ExecutionContext,
    GroundedAnswer,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    PromptProvenance,
    RetrievalProvenance,
    RunId,
    SessionId,
    SourceId,
    StudyStatementInput,
    StudyStatementKind,
    TutorContextState,
    TutorTimelineKind,
    TutorTimelineStatus,
    ValidatorProvenance,
    VerifiedRunOutputRef,
    VersionPins,
    assistant_interaction_id_for,
    session_turn_event_id_for,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.session import AnswerRecord
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.sessions import (
    SESSION_ANSWER_RECORDED,
    SESSION_ASSISTANT_TURN_RECORDED,
    SESSION_INTERACTION_RECORDED,
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    SessionService,
    SessionTurnService,
    answer_recorded_payload,
    assistant_turn_recorded_payload,
    interaction_recorded_payload,
    register_session_events,
)
from study_agent.sessions.events import (
    assistant_turn_command_fingerprint,
    tutor_message_output_fingerprint,
)
from study_agent.state import EventRegistry, canonical_json_bytes
from study_agent.study_context import (
    ProjectionStudyContextView,
    StudyContextService,
    register_study_context_events,
)
from study_agent.tutor_snapshot import TutorSnapshotReader

COURSE = CourseId("course-mixed-tutor-snapshot")
SESSION = SessionId("session-mixed-tutor-snapshot")
NOW = datetime(2026, 7, 15, 10, tzinfo=UTC)
HASH = "a" * 64


class Clock:
    def now(self) -> datetime:
        return NOW


def _context(
    key: str | None = None, *, session_id: SessionId | None = SESSION
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "snapshot-integration",
        COURSE,
        CorrelationId(f"correlation-{key or 'setup'}"),
        session_id=session_id,
        idempotency_key=key,
    )


def _event(
    sequence: int,
    event_type: str,
    payload: object,
    *,
    event_id: EventId | None = None,
) -> DomainEvent:
    from typing import cast

    from study_agent.domain._validation import JsonObject

    return DomainEvent(
        event_id or EventId(f"event-snapshot-{sequence}"),
        COURSE,
        sequence,
        event_type,
        1,
        Actor(PrincipalKind.SERVICE, "snapshot-integration"),
        NOW + timedelta(seconds=sequence),
        CorrelationId("correlation-manual-snapshot"),
        cast(JsonObject, payload),
        SESSION,
    )


def _grounded_answer() -> AnswerRecord:
    run_id = RunId("run-grounded-snapshot")
    provenance = AnswerProvenance(
        (),
        PromptProvenance("grounded_answer", "1.0.0"),
        None,
        RetrievalProvenance("sqlite_fts5", "1", HASH, "index-1", "b" * 64),
        (
            ValidatorProvenance(
                "evidence_sufficiency", "1", True, "terminate", "c" * 64
            ),
        ),
        VersionPins(
            "grounded_answer@1.0.0",
            "grounded_answer_flow@1.0.0",
            "grounded_answer@1.0.0",
            None,
            "session@1",
            "tools@1",
        ),
        run_id,
    )
    answer = GroundedAnswer(
        AnswerStatus.INSUFFICIENT_EVIDENCE,
        (),
        "The current materials do not contain enough evidence.",
        provenance,
    )
    return AnswerRecord(
        AnswerId("answer-grounded-snapshot"),
        InteractionId("assistant-grounded-snapshot"),
        InteractionId("question-grounded-snapshot"),
        run_id,
        "grounded-key",
        "d" * 64,
        answer,
    )


def test_mixed_snapshot_replays_timeline_context_and_current_materials(
    tmp_path: Path,
) -> None:
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    register_session_events(registry)
    register_study_context_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = ProjectionCourseView(events.projection)
    CourseService(events, Clock(), courses).create(
        CourseProfile(
            COURSE,
            "Anatomy",
            "en",
            exam_date=date(2026, 9, 8),
            learning_goals=("Reconstruct the brachial plexus",),
        ),
        _context(session_id=None),
    )
    ingestion = TextIngestionService(
        blobs=blobs, events=events, clock=Clock(), courses=courses
    )
    first_revision = ingestion.ingest(
        filename="notes.txt",
        content=b"First revision of the anatomy notes.",
        source_id=SourceId("source-anatomy"),
        title="Anatomy notes",
        trust_level=90,
        source_role="primary",
        context=_context(session_id=None),
    )
    second_revision = ingestion.ingest(
        filename="notes.txt",
        content=b"Second revision with the brachial plexus.",
        source_id=SourceId("source-anatomy"),
        title="Anatomy notes",
        trust_level=90,
        source_role="primary",
        context=_context(session_id=None),
    )
    selected_historical = ingestion.ingest(
        filename="notes.txt",
        content=b"First revision of the anatomy notes.",
        source_id=SourceId("source-anatomy"),
        title="Anatomy notes",
        trust_level=90,
        source_role="primary",
        context=_context(session_id=None),
    )
    assert first_revision.source.revision_id != second_revision.source.revision_id
    assert selected_historical.source.revision_id == first_revision.source.revision_id
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, courses).start(_context())
    assistant_turns = ProjectionAssistantTurnView(events.projection)
    turn_service = SessionTurnService(events, Clock(), sessions, assistant_turns)
    learner = turn_service.record_learner_turn(
        "My exam is on September 15.", _context("learner"), 5
    )
    context_view = ProjectionStudyContextView(events.projection)
    StudyContextService(events, Clock(), context_view, courses, sessions).record(
        StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 9, 15)),
        learner.id,
        _context("deadline"),
        6,
    )

    general_run = RunId("run-general-snapshot")
    content = "Understood. Let us work from your current materials."
    output = VerifiedRunOutputRef(
        general_run,
        tutor_message_output_fingerprint(
            AssistantTurnStatus.COMPLETED, content, learner.id
        ),
    )
    turn = AssistantTurnRecord(
        assistant_interaction_id_for(COURSE, SESSION, general_run, "assistant-key"),
        SESSION,
        NOW + timedelta(seconds=8),
        AssistantTurnStatus.COMPLETED,
        content,
        learner.id,
        output,
        "assistant-key",
        assistant_turn_command_fingerprint(
            AssistantTurnStatus.COMPLETED, content, learner.id, output
        ),
        session_turn_event_id_for(
            COURSE, SESSION, "assistant-key", SESSION_ASSISTANT_TURN_RECORDED
        ),
        8,
    )
    events.append(
        COURSE,
        7,
        (
            _event(
                8,
                SESSION_ASSISTANT_TURN_RECORDED,
                assistant_turn_recorded_payload(turn),
                event_id=turn.event_id,
            ),
        ),
    )
    grounded = _grounded_answer()
    events.append(
        COURSE,
        8,
        (
            _event(
                9,
                SESSION_INTERACTION_RECORDED,
                interaction_recorded_payload(
                    grounded.question_interaction_id,
                    InteractionKind.HUMAN,
                    "What does the supplied material establish?",
                ),
            ),
            _event(10, SESSION_ANSWER_RECORDED, answer_recorded_payload(grounded)),
            _event(
                11,
                SESSION_INTERACTION_RECORDED,
                interaction_recorded_payload(
                    InteractionId("note-snapshot"),
                    InteractionKind.NOTE,
                    "Revisit the roots and trunks.",
                ),
            ),
        ),
    )

    reader = TutorSnapshotReader(events, registry)
    first = reader.get(COURSE, SESSION)
    second = reader.get(COURSE, SESSION)

    assert first.high_water_sequence == 11
    assert tuple(item.kind for item in first.timeline) == (
        TutorTimelineKind.LEARNER,
        TutorTimelineKind.ASSISTANT,
        TutorTimelineKind.LEARNER,
        TutorTimelineKind.ASSISTANT,
        TutorTimelineKind.NOTE,
    )
    assert tuple(item.course_sequence for item in first.timeline) == (6, 8, 9, 10, 11)
    assert first.timeline[1].status is TutorTimelineStatus.COMPLETED
    assert first.timeline[3].status is TutorTimelineStatus.INSUFFICIENT_EVIDENCE
    assert first.timeline[3].in_reply_to_interaction_id == grounded.question_interaction_id
    assert first.notes[0].interaction_id == InteractionId("note-snapshot")
    deadline = first.learner_context[1]
    assert deadline.state is TutorContextState.KNOWN
    assert first.divergences[0].kind is StudyStatementKind.DEADLINE
    assert first.materials[0].current_revision_id == first_revision.source.revision_id
    assert first.materials[0].chunk_count == len(first_revision.chunks)
    assert canonical_json_bytes(first.to_json()) == canonical_json_bytes(second.to_json())
    assert events.verify_projection(COURSE)

    def corrupt_source_owner(
        state: JsonObject, _: DomainEvent, payload: JsonObject
    ) -> Mapping[str, JsonValue]:
        assert payload == {}
        sources = dict(cast(JsonObject, state["sources"]))
        source_projection = dict(cast(JsonObject, sources["source-anatomy"]))
        revisions = dict(cast(JsonObject, source_projection["revisions"]))
        current_revision_id = cast(str, source_projection["current_revision_id"])
        revision = dict(cast(JsonObject, revisions[current_revision_id]))
        source = dict(cast(JsonObject, revision["source"]))
        source["source_id"] = "source-other"
        revision["source"] = source
        revisions[current_revision_id] = revision
        source_projection["revisions"] = revisions
        sources["source-anatomy"] = source_projection
        return {**state, "sources": sources}

    registry.register(
        "test.corrupt_source_owner", 1, lambda payload: payload, corrupt_source_owner
    )
    events.append(
        COURSE,
        11,
        (_event(12, "test.corrupt_source_owner", {}),),
    )

    with pytest.raises(
        ValueError, match=r"source revision (projection|ownership) is corrupt"
    ):
        reader.get(COURSE, SESSION)
