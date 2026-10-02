from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from study_agent.adapters.sqlite import SequenceConflictError, SQLiteEventStore
from study_agent.domain import (
    Actor,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    PrincipalKind,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports import EventStore
from study_agent.ports.storage import EventSequenceConflictError
from study_agent.state import EventRegistry


def make_event(course_id: CourseId, sequence: int) -> DomainEvent:
    return DomainEvent(
        EventId(f"event-{sequence}"),
        course_id,
        sequence,
        "counter.incremented",
        1,
        Actor(PrincipalKind.SERVICE, "test-suite"),
        datetime(2026, 7, 10, 12, sequence, tzinfo=UTC),
        CorrelationId("correlation-1"),
        {"amount": sequence},
    )


def registry() -> EventRegistry:
    result = EventRegistry()

    def decode(payload: JsonObject) -> int:
        amount = payload.get("amount")
        if not isinstance(amount, int):
            raise ValueError("amount must be an integer")
        return amount

    def increment(
        state: JsonObject, _: DomainEvent, amount: int
    ) -> Mapping[str, JsonValue]:
        total = state.get("total", 0)
        assert isinstance(total, int)
        return {"total": total + amount}

    result.register("counter.incremented", 1, decode, increment)
    return result


def exercise_event_store_contract(store: EventStore) -> None:
    course_id = CourseId("course-contract")
    events = (make_event(course_id, 1), make_event(course_id, 2))

    assert store.read(course_id) == ()
    assert store.append(course_id, 0, events) == 2
    assert store.read(course_id) == events
    assert store.read(course_id, after_sequence=1) == (events[1],)
    assert store.append(course_id, 2, ()) == 2


def test_sqlite_adapter_conforms_to_event_store_port(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3", registry())
    exercise_event_store_contract(store)


def test_event_schema_cannot_be_registered_without_a_payload_decoder() -> None:
    result = EventRegistry()

    def reducer(
        state: JsonObject, _: DomainEvent, __: object
    ) -> Mapping[str, JsonValue]:
        return state

    with pytest.raises(TypeError):
        result.register("counter.incremented", 1, reducer)  # type: ignore[call-arg]


def test_sqlite_conflict_implements_portable_sequence_conflict(tmp_path: Path) -> None:
    store = SQLiteEventStore(tmp_path / "events.sqlite3", registry())
    course_id = CourseId("course-conflict")
    store.append(course_id, 0, (make_event(course_id, 1),))

    with pytest.raises(EventSequenceConflictError) as caught:
        store.append(course_id, 0, ())

    assert isinstance(caught.value, SequenceConflictError)
    assert (caught.value.expected, caught.value.actual) == (0, 1)


def test_repeated_reads_observe_external_append_and_projection_byte_changes(tmp_path: Path) -> None:
    path = tmp_path / "events.sqlite3"
    course_id = CourseId("cached-course")
    store = SQLiteEventStore(path, registry())
    store.append(course_id, 0, (make_event(course_id, 1),))
    assert store.projection(course_id).sequence == 1
    assert store.read(course_id) == (make_event(course_id, 1),)

    external = SQLiteEventStore(path, registry())
    external.append(course_id, 1, (make_event(course_id, 2),))
    assert store.projection(course_id).sequence == 2
    assert store.read(course_id) == (make_event(course_id, 1), make_event(course_id, 2))
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE projections SET state = ? WHERE course_id = ?", (
            b"not-json", str(course_id),
        ))
    with pytest.raises(ValueError):
        store.projection(course_id)
