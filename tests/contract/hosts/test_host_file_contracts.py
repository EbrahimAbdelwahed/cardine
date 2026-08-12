from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

import pytest

from cardine.hosts.files import (
    HostFileError,
    HostFileReference,
    HostFileSnapshot,
    TrustedHostFileIngestionCommand,
)
from study_agent.adapters.memory import MemoryHostFileSnapshotStore
from study_agent.domain import CorrelationId, CourseId, SessionId, SourceId
from study_agent.domain.context import ExecutionContext
from study_agent.domain.events import PrincipalKind


def _context(course: CourseId, session: SessionId) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "host",
        course,
        CorrelationId("correlation"),
        session_id=session,
    )


def test_snapshot_rejects_path_media_and_utf8_contract_violations() -> None:
    digest = sha256(b"content").hexdigest()
    args = (
        "id",
        CourseId("course"),
        SessionId("session"),
        "display",
        "text/plain",
        "lesson.txt",
        7,
        digest,
        b"content",
        datetime(2026, 7, 18, tzinfo=UTC),
        datetime(2026, 7, 19, tzinfo=UTC),
    )
    assert HostFileSnapshot(*args).byte_size == 7
    with pytest.raises(HostFileError):
        HostFileSnapshot(
            args[0], args[1], args[2], args[3], args[4], "../lesson.txt", *args[6:]
        )
    with pytest.raises(HostFileError):
        HostFileSnapshot(
            args[0], args[1], args[2], args[3], "text/markdown", *args[5:]
        )


def test_memory_store_checks_bounds_before_mutation_and_allows_exact_retry() -> None:
    store = MemoryHostFileSnapshotStore(max_snapshot_count=1, max_aggregate_bytes=5)
    snapshot = HostFileSnapshot(
        "one",
        CourseId("course"),
        SessionId("session"),
        "display",
        "text/plain",
        "lesson.txt",
        5,
        sha256(b"12345").hexdigest(),
        b"12345",
        datetime(2026, 7, 18, tzinfo=UTC),
        datetime(2026, 7, 19, tzinfo=UTC),
    )
    payload = snapshot.to_bytes()
    assert store.create("one", payload)
    assert not store.create("one", payload)
    with pytest.raises(ValueError):
        store.create("two", payload)
    assert store.snapshot_count == 1
    assert store.aggregate_bytes == 5


def test_memory_store_rejects_tampered_snapshot_before_mutation() -> None:
    store = MemoryHostFileSnapshotStore(max_snapshot_count=2, max_aggregate_bytes=5)
    with pytest.raises(ValueError):
        store.create("one", b'{"byte_size":0}')
    assert store.snapshot_count == 0
    assert store.aggregate_bytes == 0


def test_trusted_ingestion_command_cannot_cross_context_owner() -> None:
    course, session = CourseId("course"), SessionId("session")
    reference = HostFileReference(course, session, "id", sha256(b"x").hexdigest())
    with pytest.raises(HostFileError):
        TrustedHostFileIngestionCommand(
            reference,
            SourceId("source"),
            "Lesson",
            50,
            "lesson",
            _context(CourseId("other"), session),
        )
    command = TrustedHostFileIngestionCommand(
        reference,
        SourceId("source"),
        "Lesson",
        50,
        "lesson",
        _context(course, session),
        expected_sequence=0,
    )
    assert command.expected_sequence == 0
