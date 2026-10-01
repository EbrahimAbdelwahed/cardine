from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from cardine.application.legacy_student_state import (
    without_study_memory_summary,
)
from cardine.application.student_state import StudentStateService
from cardine.courses import ProjectionCourseView, register_course_events
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    InteractionRecord,
    PrincipalKind,
    SessionId,
)
from study_agent.domain._validation import JsonObject
from study_agent.sessions import (
    SESSION_INTERACTION_RECORDED,
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    SessionService,
    SessionTurnService,
    register_session_events,
)
from study_agent.sessions.service import IdempotencyConflictError
from study_agent.state import EventRegistry, canonical_json_bytes
from tests.course_fixtures import create_canonical_course

COURSE = CourseId("study-memory-course")
SESSION_ONE = SessionId("study-memory-session-one")
SESSION_TWO = SessionId("study-memory-session-two")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 8, 14, 15, tzinfo=UTC)


def test_structured_memory_is_removed_from_grounding_continuation_context() -> None:
    private_note = 'study-memory@1:{"summary":"contenuto privato"}'
    spoofed_note = 'study-memory@1:{"summary":"testo umano ordinario"}'
    cleaned = without_study_memory_summary(
        {
            "unresolved_notes": (
                "Una nota ordinaria.",
                private_note,
                spoofed_note,
            ),
            "grounded_points": ("Punto verificato.",),
            "recent_exchanges": (),
        },
        frozenset({private_note}),
    )

    assert cleaned["unresolved_notes"] == ("Una nota ordinaria.", spoofed_note)
    assert "contenuto privato" not in str(cleaned)


def _context(
    *,
    session_id: SessionId,
    correlation: str,
    principal_kind: PrincipalKind = PrincipalKind.SERVICE,
    principal_id: str = "study-agent-tutor-tool-host",
) -> ExecutionContext:
    return ExecutionContext(
        principal_kind,
        principal_id,
        COURSE,
        CorrelationId(correlation),
        session_id=session_id,
        idempotency_key=correlation,
    )


def _archive(
    tmp_path: Path,
) -> tuple[
    StudentStateService,
    SessionService,
    ProjectionSessionView,
    SQLiteEventStore,
    SessionTurnService,
]:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    create_canonical_course(events, COURSE)
    sessions = ProjectionSessionView(events.projection)
    service = SessionService(events, Clock(), sessions, ProjectionCourseView(events.projection))
    service.start(_context(session_id=SESSION_ONE, correlation="start-one"))
    service.start(_context(session_id=SESSION_TWO, correlation="start-two"))
    turns = SessionTurnService(
        events,
        Clock(),
        sessions,
        ProjectionAssistantTurnView(events.projection),
    )
    return (
        StudentStateService(tmp_path / "student-state.json", events, sessions, Clock()),
        service,
        sessions,
        events,
        turns,
    )


def _learner_event_sequence(
    events: SQLiteEventStore,
    interaction: InteractionRecord,
    session_id: SessionId,
) -> int:
    for event in events.read(COURSE):
        if (
            event.event_type == SESSION_INTERACTION_RECORDED
            and event.session_id == session_id
            and event.actor.kind is PrincipalKind.HUMAN
            and event.payload.get("kind") == "human"
            and event.payload.get("interaction_id") == str(interaction.id)
        ):
            return event.course_sequence
    raise AssertionError(f"learner interaction {interaction.id} was not persisted")


def test_service_memory_round_trip_is_course_wide_and_high_water_bounded(
    tmp_path: Path,
) -> None:
    archive, _, _, events, turns = _archive(tmp_path)

    learner_one = turns.record_learner_turn(
        "Non ricordo la cinetica enzimatica.",
        _context(
            session_id=SESSION_ONE,
            correlation="learner-one",
            principal_kind=PrincipalKind.HUMAN,
            principal_id="learner",
        ),
        expected_sequence=events.read(COURSE)[-1].course_sequence,
    )
    first_origin_sequence = _learner_event_sequence(events, learner_one, SESSION_ONE)

    first = archive.record_learner_signal(
        topic="cinetica enzimatica",
        summary="Ha confuso Km e Vmax.",
        signal="partial",
        assistance="explanation",
        context=_context(session_id=SESSION_ONE, correlation="memory-one"),
        origin_sequence=first_origin_sequence,
    )

    learner_two = turns.record_learner_turn(
        "Ora ripasso la glicolisi.",
        _context(
            session_id=SESSION_TWO,
            correlation="learner-two",
            principal_kind=PrincipalKind.HUMAN,
            principal_id="learner",
        ),
        expected_sequence=events.read(COURSE)[-1].course_sequence,
    )
    second_origin_sequence = _learner_event_sequence(events, learner_two, SESSION_TWO)
    second = archive.record_topic_covered(
        topic="glicolisi",
        context=_context(
            session_id=SESSION_TWO,
            correlation="memory-two",
            principal_id="cardine-tutor-host",
        ),
        origin_sequence=second_origin_sequence,
    )

    assert first.origin_sequence == first_origin_sequence
    assert second.origin_sequence == second_origin_sequence

    all_entries = archive.search(COURSE)
    assert {entry.event_id for entry in all_entries} == {
        first.event_id,
        second.event_id,
    }
    assert {entry.topic for entry in all_entries} == {"cinetica enzimatica", "glicolisi"}
    assert archive.search(COURSE, query="cinetica") == (first,)

    through_first = archive.search(
        COURSE,
        through_sequence=first.origin_sequence,
    )
    assert through_first == (first,)

    next_turn = turns.record_learner_turn(
        "Continuiamo a studiare.",
        _context(
            session_id=SESSION_TWO,
            correlation="learner-after-memory",
            principal_kind=PrincipalKind.HUMAN,
            principal_id="learner",
        ),
        expected_sequence=events.read(COURSE)[-1].course_sequence,
    )
    assert next_turn.content == "Continuiamo a studiare."


def test_reads_do_not_create_file_and_restart_preserves_history(tmp_path: Path) -> None:
    archive, _, sessions, events, _ = _archive(tmp_path)
    assert archive.get(COURSE).entries == ()
    assert not archive.path.exists()
    context = _context(
        session_id=SESSION_ONE, correlation="manual", principal_kind=PrincipalKind.HUMAN
    )
    before = tuple(events.read(COURSE))
    entry = archive.record_topic_covered(topic="glicolisi", context=context, origin_sequence=0)
    prefix = archive.path.read_bytes()
    assert (
        archive.record_topic_covered(topic="glicolisi", context=context, origin_sequence=0) == entry
    )
    assert archive.path.read_bytes() == prefix
    restarted = StudentStateService(archive.path, events, sessions, Clock())
    assert restarted.search(COURSE) == (entry,)
    assert tuple(events.read(COURSE)) == before  # no hidden session note writes
    assert restarted.search(CourseId("another-course")) == ()
    with pytest.raises(IdempotencyConflictError):
        restarted.record_topic_covered(topic="enzimi", context=context, origin_sequence=0)
    assert archive.path.read_bytes() == prefix


def test_concurrent_appends_have_unique_sequences_and_keep_all_events(tmp_path: Path) -> None:
    archive, _, _, _, _ = _archive(tmp_path)

    def record(number: int) -> object:
        return archive.record_topic_covered(
            topic=f"Topic {number}",
            origin_sequence=0,
            context=_context(
                session_id=SESSION_ONE,
                correlation=f"parallel-{number}",
                principal_kind=PrincipalKind.HUMAN,
            ),
        )

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(record, range(24)))
    records = archive.get(COURSE).entries
    assert len(records) == 24
    assert tuple(e.sequence for e in records) == tuple(range(1, 25))
    assert len({e.event_id for e in records}) == 24
    page = archive.get(COURSE).to_json(limit=8)
    assert page["has_more"] is True
    assert page["next_cursor"] == 17
    prior = archive.get(COURSE).to_json(limit=8, before_sequence=17)
    assert tuple(e["sequence"] for e in cast(tuple[JsonObject, ...], prior["entries"])) == tuple(
        range(16, 8, -1)
    )


@pytest.mark.parametrize("tail", [b'{"partial":', b"not json\n", b"\n"])
def test_corrupt_tail_is_preserved_and_blocks_further_appends(tmp_path: Path, tail: bytes) -> None:
    archive, _, _, _, _ = _archive(tmp_path)
    archive.path.write_bytes(tail)
    context = _context(
        session_id=SESSION_ONE, correlation="invalid-tail", principal_kind=PrincipalKind.HUMAN
    )
    with pytest.raises(ValueError):
        archive.get(COURSE)
    with pytest.raises(ValueError):
        archive.record_topic_covered(topic="topic", context=context, origin_sequence=0)
    assert archive.path.read_bytes() == tail


def test_symlink_and_hardlink_journals_are_rejected(tmp_path: Path) -> None:
    archive, _, _, _, _ = _archive(tmp_path)
    outside = tmp_path / "outside.json"
    outside.write_bytes(b"")
    archive.path.symlink_to(outside)
    with pytest.raises(OSError):
        archive.get(COURSE)
    archive.path.unlink()
    os.link(outside, archive.path)
    with pytest.raises(ValueError):
        archive.get(COURSE)
    assert outside.read_bytes() == b""


def test_model_and_untrusted_service_cannot_write_observations(tmp_path: Path) -> None:
    archive, _, _, _, _ = _archive(tmp_path)
    for kind, writer in ((PrincipalKind.MODEL, "model"), (PrincipalKind.SERVICE, "untrusted")):
        with pytest.raises(ValueError, match="not trusted"):
            archive.record_topic_covered(
                topic="topic",
                origin_sequence=0,
                context=_context(
                    session_id=SESSION_ONE,
                    correlation="unauthorized",
                    principal_kind=kind,
                    principal_id=writer,
                ),
            )
    assert not archive.path.exists()


def test_import_preserves_legacy_notes_and_ignores_spoofed_origins(tmp_path: Path) -> None:
    archive, service, _, events, turns = _archive(tmp_path)
    learner = turns.record_learner_turn(
        "Fatico con gli enzimi.",
        _context(session_id=SESSION_ONE, correlation="origin", principal_kind=PrincipalKind.HUMAN),
        expected_sequence=events.read(COURSE)[-1].course_sequence,
    )
    origin = _learner_event_sequence(events, learner, SESSION_ONE)
    value: JsonObject = {
        "assistance": "none",
        "kind": "learner_signal",
        "origin_interaction_id": str(learner.id),
        "origin_sequence": origin,
        "recorded_by": "tutor_agent",
        "schema_version": 1,
        "signal": "self_reported_difficulty",
        "summary": "Difficoltà dichiarata.",
        "topic": "enzimi",
    }
    content = "study-memory@1:" + canonical_json_bytes(value).decode()
    service.record_note(_context(session_id=SESSION_ONE, correlation="old-note"), content)
    service.record_note(
        _context(session_id=SESSION_ONE, correlation="spoof", principal_kind=PrincipalKind.HUMAN),
        content,
    )
    before = tuple(events.read(COURSE))
    result = archive.import_history(COURSE)
    assert len(result.entries) == 1
    assert result.entries[0].topic == "enzimi"
    file_before = archive.path.read_bytes()
    assert archive.import_history(COURSE) == result
    assert archive.path.read_bytes() == file_before
    assert tuple(events.read(COURSE)) == before
