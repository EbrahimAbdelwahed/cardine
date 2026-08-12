from __future__ import annotations

from collections.abc import Sequence
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from hashlib import sha256

from cardine.hosts.files import (
    HostFileError,
    HostFileReference,
    HostFileRegistry,
    TrustedHostFileIngestionCommand,
)
from study_agent.adapters.memory import MemoryHostFileIdentity, MemoryHostFileSnapshotStore
from study_agent.domain import CorrelationId, CourseId, SessionId, SourceId
from study_agent.domain.context import ExecutionContext
from study_agent.domain.events import PrincipalKind
from study_agent.ports.source_input import SourceSnapshot


class Source:
    def snapshot(self, path: str) -> SourceSnapshot:
        content = b"# lesson"
        return SourceSnapshot(path, content, sha256(content).hexdigest(), len(content))

    def snapshots(self, paths: Sequence[str]) -> tuple[SourceSnapshot, ...]:
        return tuple(self.snapshot(path) for path in paths)


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 18, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


class Ingestion:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def ingest(self, **kwargs: object) -> str:
        self.calls.append(kwargs)
        return "ingested"


def test_trusted_bridge_delegates_exact_metadata_and_expiry_blocks_calls() -> None:
    course, session = CourseId("course"), SessionId("session")
    clock = Clock()
    registry = HostFileRegistry(
        Source(),
        MemoryHostFileIdentity(),
        MemoryHostFileSnapshotStore(),
        clock,
        timedelta(minutes=10),
    )
    descriptor = registry.capture("lesson.md", course, session, "Shown lesson")
    reference = HostFileReference(course, session, descriptor.id, descriptor.checksum_sha256)
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "host",
        course,
        CorrelationId("correlation"),
        session_id=session,
    )
    command = TrustedHostFileIngestionCommand(
        reference, SourceId("source"), "Lesson", 80, "required", context, 2
    )
    ingestion = Ingestion()

    assert registry.ingest(command, ingestion) == "ingested"
    assert ingestion.calls == [
        {
            "filename": "lesson.md",
            "content": b"# lesson",
            "source_id": SourceId("source"),
            "title": "Lesson",
            "trust_level": 80,
            "source_role": "required",
            "context": context,
            "expected_sequence": 2,
        }
    ]

    clock.current += timedelta(minutes=10)
    with suppress(HostFileError):
        registry.ingest(command, ingestion)
    assert len(ingestion.calls) == 1
