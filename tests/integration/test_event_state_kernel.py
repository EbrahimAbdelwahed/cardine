from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from study_agent.adapters.sqlite import (
    SequenceConflictError,
    SQLiteEventStore,
    UnsupportedSQLiteDatabaseError,
)
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    PrincipalKind,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.state import EventRegistry, PayloadValidationError


def make_event(
    sequence: int,
    event_type: str = "note.recorded",
    payload: Mapping[str, JsonValue] | None = None,
) -> DomainEvent:
    return DomainEvent(
        EventId(f"event-{sequence}-{event_type}"),
        CourseId("course-1"),
        sequence,
        event_type,
        1,
        Actor(PrincipalKind.HUMAN, "local-user"),
        datetime(2026, 7, 10, 12, sequence, tzinfo=UTC),
        CorrelationId("correlation-1"),
        payload if payload is not None else {"note": f"note-{sequence}"},
    )


def note_registry() -> EventRegistry:
    registry = EventRegistry()

    def decode(payload: JsonObject) -> str:
        note = payload.get("note")
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        return note

    def record(
        state: JsonObject, _: DomainEvent, note: str
    ) -> Mapping[str, JsonValue]:
        notes = state.get("notes", ())
        assert isinstance(notes, tuple)
        return {"notes": (*notes, note)}

    registry.register("note.recorded", 1, decode, record)
    return registry


def typed_note_registry() -> EventRegistry:
    return note_registry()


def test_in_memory_database_is_rejected_with_supported_contract_error() -> None:
    with pytest.raises(UnsupportedSQLiteDatabaseError, match="path-backed"):
        SQLiteEventStore(":memory:", note_registry())


def test_expected_sequence_conflict_has_no_partial_mutation(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3", note_registry())
    first = make_event(1)
    store.append(first.course_id, 0, (first,))
    before = store.projection_bytes(first.course_id)

    with pytest.raises(SequenceConflictError) as raised:
        store.append(first.course_id, 0, (make_event(1, "other.event"),))

    assert (raised.value.expected, raised.value.actual) == (0, 1)
    assert store.read(first.course_id) == (first,)
    assert store.projection_bytes(first.course_id) == before


def test_later_reducer_failure_rolls_back_entire_event_batch(tmp_path: Path) -> None:
    registry = note_registry()

    def decode_failure(payload: JsonObject) -> str:
        note = payload.get("note")
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        return note

    def fail(_: JsonObject, __: DomainEvent, ___: str) -> Mapping[str, JsonValue]:
        raise RuntimeError("projection failed")

    registry.register("projection.failed", 1, decode_failure, fail)
    store = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    first = make_event(1)

    with pytest.raises(RuntimeError, match="projection failed"):
        store.append(first.course_id, 0, (first, make_event(2, "projection.failed")))

    assert store.read(first.course_id) == ()
    assert store.projection(first.course_id).sequence == 0


def test_later_malformed_payload_rejects_entire_batch_before_insert(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3", typed_note_registry())
    course_id = CourseId("course-1")

    with pytest.raises(PayloadValidationError, match="note must be a string"):
        store.append(
            course_id,
            0,
            (make_event(1), make_event(2, payload={"note": 2})),
        )

    assert store.read(course_id) == ()
    assert store.projection(course_id).sequence == 0


def test_projection_rebuild_is_byte_identical_and_event_rows_are_append_only(
    tmp_path: Path,
) -> None:
    database = tmp_path / "events.sqlite3"
    store = SQLiteEventStore(database, note_registry())
    events = (make_event(1), make_event(2))
    store.append(events[0].course_id, 0, events)
    original = store.projection_bytes(events[0].course_id)

    assert store.verify_projection(events[0].course_id)
    assert store.rebuild_projection(events[0].course_id) == original
    assert store.projection_bytes(events[0].course_id) == original

    with sqlite3.connect(database) as connection, pytest.raises(
        sqlite3.IntegrityError, match="append-only"
    ):
        connection.execute("DELETE FROM events WHERE course_id = ?", (str(events[0].course_id),))


def test_projection_at_replays_only_the_requested_canonical_prefix(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3", note_registry())
    course_id = CourseId("course-1")
    store.append(course_id, 0, (make_event(1), make_event(2)))

    assert store.projection_at(course_id, 0).sequence == 0
    first = store.projection_at(course_id, 1)
    assert first.sequence == 1
    assert first.state["notes"] == ("note-1",)
    assert store.projection_at(course_id, 2).state["notes"] == ("note-1", "note-2")

    with pytest.raises(ValueError, match="non-negative"):
        store.projection_at(course_id, -1)


def test_failed_rebuild_preserves_previous_projection(tmp_path: Path) -> None:
    should_fail = False
    registry = EventRegistry()

    def decode(payload: JsonObject) -> str:
        note = payload.get("note")
        if not isinstance(note, str):
            raise ValueError("note must be a string")
        return note

    def record(
        state: JsonObject, _: DomainEvent, note: str
    ) -> Mapping[str, JsonValue]:
        if should_fail:
            raise RuntimeError("replay failed")
        return {"last_note": note}

    registry.register("note.recorded", 1, decode, record)
    store = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    first = make_event(1)
    store.append(first.course_id, 0, (first,))
    before = store.projection_bytes(first.course_id)
    should_fail = True

    with pytest.raises(RuntimeError, match="replay failed"):
        store.rebuild_projection(first.course_id)

    assert store.projection_bytes(first.course_id) == before
