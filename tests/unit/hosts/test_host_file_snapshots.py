from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256

import pytest

from cardine.hosts.files import (
    HostFileError,
    HostFileReference,
    HostFileRegistry,
    HostFileSnapshot,
)
from study_agent.adapters.memory import MemoryHostFileIdentity, MemoryHostFileSnapshotStore
from study_agent.domain import CourseId, SessionId
from study_agent.ports.source_input import SourceSnapshot


class Source:
    def __init__(self, content: bytes = b"# lesson") -> None:
        self.content = content
        self.calls = 0

    def snapshot(self, relative_path: str) -> SourceSnapshot:
        self.calls += 1
        return SourceSnapshot(
            relative_path,
            self.content,
            sha256(self.content).hexdigest(),
            len(self.content),
        )

    def snapshots(self, relative_paths: Sequence[str]) -> tuple[SourceSnapshot, ...]:
        return tuple(self.snapshot(path) for path in relative_paths)


class Clock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 18, tzinfo=UTC)

    def now(self) -> datetime:
        return self.current


def make_registry(source: Source, clock: Clock) -> HostFileRegistry:
    return HostFileRegistry(
        source,
        MemoryHostFileIdentity(),
        MemoryHostFileSnapshotStore(),
        clock,
        timedelta(hours=1),
    )


def test_capture_is_single_read_and_retry_is_stable() -> None:
    source = Source()
    clock = Clock()
    registry = make_registry(source, clock)
    course, session = CourseId("course"), SessionId("session")

    first = registry.capture("nested/lesson.md", course, session, "Lesson")
    clock.current += timedelta(minutes=5)
    retry = registry.capture("nested/lesson.md", course, session, "Lesson")

    assert source.calls == 2
    assert retry == first
    assert registry.lookup(
        HostFileReference(course, session, first.id, first.checksum_sha256)
    ).is_untrusted


def test_snapshot_codec_is_canonical_and_tamper_evident() -> None:
    content = b"hello"
    snapshot = HostFileSnapshot(
        "opaque-id",
        CourseId("course"),
        SessionId("session"),
        "notes",
        "text/plain",
        "notes.txt",
        len(content),
        sha256(content).hexdigest(),
        content,
        datetime(2026, 7, 18, tzinfo=UTC),
        datetime(2026, 7, 19, tzinfo=UTC),
    )
    assert HostFileSnapshot.from_bytes(snapshot.to_bytes()).to_bytes() == snapshot.to_bytes()
    with pytest.raises(HostFileError):
        HostFileSnapshot.from_bytes(snapshot.to_bytes() + b" ")
    noncanonical = json.loads(snapshot.to_bytes())
    noncanonical["content_base64"] = "aGVsbG9="
    with pytest.raises(HostFileError, match="semantically canonical"):
        HostFileSnapshot.from_bytes(
            json.dumps(noncanonical, sort_keys=True, separators=(",", ":")).encode()
        )


def test_lookup_rejects_cross_owner_checksum_and_expiry() -> None:
    source = Source()
    clock = Clock()
    registry = make_registry(source, clock)
    course, session = CourseId("course"), SessionId("session")
    descriptor = registry.capture("lesson.txt", course, session, "Lesson")

    with pytest.raises(HostFileError):
        registry.lookup(
            HostFileReference(
                CourseId("other"), session, descriptor.id, descriptor.checksum_sha256
            )
        )
    with pytest.raises(HostFileError):
        registry.lookup(
            HostFileReference(course, session, descriptor.id, sha256(b"other").hexdigest())
        )
    clock.current += timedelta(hours=1)
    with pytest.raises(HostFileError):
        registry.lookup(
            HostFileReference(course, session, descriptor.id, descriptor.checksum_sha256)
        )


def test_capture_does_not_return_expired_snapshot_or_rotate_it() -> None:
    source = Source()
    clock = Clock()
    registry = make_registry(source, clock)
    course, session = CourseId("course"), SessionId("session")
    descriptor = registry.capture("lesson.txt", course, session, "Lesson")
    clock.current += timedelta(hours=1)
    with pytest.raises(HostFileError, match="expired"):
        registry.capture("lesson.txt", course, session, "Lesson")
    with pytest.raises(HostFileError, match="expired"):
        registry.capture("lesson.txt", course, session, "Lesson")
    assert source.calls == 3
    with pytest.raises(HostFileError):
        registry.lookup(
            HostFileReference(course, session, descriptor.id, descriptor.checksum_sha256)
        )
