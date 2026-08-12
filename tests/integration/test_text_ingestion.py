from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.courses import register_course_events
from study_agent.domain import (
    Actor,
    BlobId,
    BlobRef,
    CorrelationId,
    CourseId,
    DomainEvent,
    ExecutionContext,
    PrincipalKind,
    SourceId,
)
from study_agent.ingestion import (
    CHUNKER_VERSION,
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_SELECTED,
    ChunkingConfig,
    IngestionErrorCode,
    IngestionStatus,
    TextIngestionError,
    TextIngestionResult,
    TextIngestionService,
    chunk_text,
    decode_source_revision_ingested,
    register_source_revision_events,
    source_event_id_for,
    source_revision_payload,
)
from study_agent.ingestion.identity import legacy_revision_id_for
from study_agent.ports.storage import EventSequenceConflictError
from study_agent.state import EventRegistry
from tests.course_fixtures import ExistingCourseView, create_canonical_course


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 11, 10, 30, tzinfo=UTC)


def context() -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "trusted-ingestion",
        CourseId("course-1"),
        CorrelationId("correlation-1"),
    )


def make_service(
    tmp_path: Path,
) -> tuple[TextIngestionService, FilesystemBlobStore, SQLiteEventStore]:
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = create_canonical_course(events, context().course_id)
    return (
        TextIngestionService(
            blobs=blobs, events=events, clock=FixedClock(), courses=courses
        ),
        blobs,
        events,
    )


def ingest(service: TextIngestionService, content: bytes) -> TextIngestionResult:
    return service.ingest(
        filename="cardiology.md",
        content=content,
        source_id=SourceId("source-cardiology"),
        title="Cardiology notes",
        trust_level=90,
        source_role="primary",
        context=context(),
    )


def test_ingestion_preserves_original_and_normalized_bytes_and_exact_event(
    tmp_path: Path,
) -> None:
    service, blobs, events = make_service(tmp_path)
    original = "# Heart\r\n\r\nCafe\u0301 myocardium.".encode()

    result = ingest(service, original)

    assert result.status is IngestionStatus.EMITTED
    assert result.committed_sequence == 2
    assert blobs.get(result.source.blob) == original
    normalized = blobs.get(result.source.normalized_blob)
    assert normalized == "# Heart\n\nCafé myocardium.".encode()
    normalized_text = normalized.decode()
    assert all(
        normalized_text[chunk.start_offset : chunk.end_offset]
        for chunk in result.chunks
    )
    event = events.read(context().course_id)[1]
    decoded = decode_source_revision_ingested(event.payload)
    assert decoded.source == result.source
    assert decoded.chunks == result.chunks
    assert event.actor.principal_id == "trusted-ingestion"
    assert event.correlation_id == context().correlation_id
    assert events.verify_projection(context().course_id)
    blobs.close()


def test_identical_ingestion_is_idempotent_and_changed_bytes_create_revision(
    tmp_path: Path,
) -> None:
    service, blobs, events = make_service(tmp_path)
    first = ingest(service, b"# Heart\n\nFirst revision.")
    identical = ingest(service, b"# Heart\n\nFirst revision.")
    changed = ingest(service, b"# Heart\n\nChanged revision.")

    assert first.status is IngestionStatus.EMITTED
    assert identical.status is IngestionStatus.IDEMPOTENT
    assert identical.source.revision_id == first.source.revision_id
    assert identical.committed_sequence == 2
    assert changed.status is IngestionStatus.EMITTED
    assert changed.source.revision_id != first.source.revision_id
    assert changed.committed_sequence == 3
    assert len(events.read(context().course_id)) == 3
    assert blobs.get(first.source.blob) == b"# Heart\n\nFirst revision."
    assert events.verify_projection(context().course_id)
    blobs.close()


@pytest.mark.parametrize(
    ("filename", "content", "code"),
    [
        ("notes.pdf", b"content", IngestionErrorCode.UNSUPPORTED_EXTENSION),
        ("notes.txt", b"\xff", IngestionErrorCode.INVALID_UTF8),
    ],
)
def test_invalid_extension_and_utf8_append_no_event(
    tmp_path: Path,
    filename: str,
    content: bytes,
    code: IngestionErrorCode,
) -> None:
    service, blobs, events = make_service(tmp_path)

    with pytest.raises(TextIngestionError) as caught:
        service.ingest(
            filename=filename,
            content=content,
            source_id=SourceId("source-1"),
            title="Notes",
            trust_level=50,
            source_role="primary",
            context=context(),
        )

    assert caught.value.code is code
    assert len(events.read(context().course_id)) == 1
    blobs.close()


class ConflictEventStore:
    def read(
        self, course_id: CourseId, after_sequence: int = 0
    ) -> Sequence[DomainEvent]:
        return ()

    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: Sequence[DomainEvent],
    ) -> int:
        raise EventSequenceConflictError(course_id, expected_sequence, expected_sequence + 1)


def test_sequence_conflict_is_structured_retryable_and_blobs_remain_reusable(
    tmp_path: Path,
) -> None:
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    service = TextIngestionService(
        blobs=blobs,
        events=ConflictEventStore(),
        clock=FixedClock(),
        courses=ExistingCourseView(),
    )

    with pytest.raises(TextIngestionError) as caught:
        ingest(service, b"Conflict content")

    assert caught.value.code is IngestionErrorCode.SEQUENCE_CONFLICT
    assert caught.value.retryable
    blobs.close()


class CountingBlobStore:
    def __init__(self, *, mismatch: bool = False) -> None:
        self.puts: list[bytes] = []
        self.contents: dict[str, bytes] = {}
        self.mismatch = mismatch

    def put(self, content: bytes) -> BlobRef:
        self.puts.append(content)
        digest = sha256(content).hexdigest()
        if self.mismatch:
            digest = "0" * 64
        ref = BlobRef(BlobId(f"sha256:{digest}"), digest, len(content))
        self.contents[str(ref.id)] = content
        return ref

    def get(self, ref: BlobRef) -> bytes:
        return self.contents[str(ref.id)]


class MemoryEventStore:
    def __init__(self) -> None:
        self.events: list[DomainEvent] = []

    def read(
        self, course_id: CourseId, after_sequence: int = 0
    ) -> Sequence[DomainEvent]:
        return tuple(
            event
            for event in self.events
            if event.course_id == course_id and event.course_sequence > after_sequence
        )

    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: Sequence[DomainEvent],
    ) -> int:
        batch = tuple(events)
        self.events.extend(batch)
        return batch[-1].course_sequence if batch else expected_sequence


class ConcurrentIdenticalEventStore(MemoryEventStore):
    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: Sequence[DomainEvent],
    ) -> int:
        self.events.extend(events)
        raise EventSequenceConflictError(course_id, expected_sequence, expected_sequence + 1)


@pytest.mark.parametrize(
    ("content", "title", "code"),
    [
        (b" \n\t", "Notes", IngestionErrorCode.INVALID_CONTENT),
        (b"valid", " ", IngestionErrorCode.INVALID_CONTENT),
    ],
)
def test_invalid_domain_or_whitespace_content_writes_no_blobs_or_events(
    content: bytes, title: str, code: IngestionErrorCode
) -> None:
    blobs = CountingBlobStore()
    events = MemoryEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )

    with pytest.raises(TextIngestionError) as caught:
        service.ingest(
            filename="notes.txt",
            content=content,
            source_id=SourceId("source-1"),
            title=title,
            trust_level=50,
            source_role="primary",
            context=context(),
        )

    assert caught.value.code is code
    assert blobs.puts == []
    assert events.events == []


def test_blob_reference_mismatch_is_structured_and_prevents_event_append() -> None:
    blobs = CountingBlobStore(mismatch=True)
    events = MemoryEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )

    with pytest.raises(TextIngestionError) as caught:
        ingest(service, b"Valid content")

    assert caught.value.code is IngestionErrorCode.BLOB_MISMATCH
    assert len(blobs.puts) == 1
    assert events.events == []


def test_metadata_changes_emit_one_revision_without_duplicate_blob_writes() -> None:
    blobs = CountingBlobStore()
    events = MemoryEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )
    first = ingest(service, b"Same immutable bytes")
    puts_after_first = len(blobs.puts)

    second = service.ingest(
        filename="cardiology.md",
        content=b"Same immutable bytes",
        source_id=SourceId("source-cardiology"),
        title="Changed title",
        trust_level=1,
        source_role="supplement",
        context=context(),
    )

    assert second.status is IngestionStatus.EMITTED
    assert second.source.revision_id != first.source.revision_id
    assert second.source.title == "Changed title"
    assert len(blobs.puts) == puts_after_first
    assert len(events.events) == 2

    identical = service.ingest(
        filename="cardiology.md",
        content=b"Same immutable bytes",
        source_id=SourceId("source-cardiology"),
        title="Changed title",
        trust_level=1,
        source_role="supplement",
        context=context(),
    )
    assert identical.status is IngestionStatus.IDEMPOTENT
    assert identical.source.revision_id == second.source.revision_id
    assert len(blobs.puts) == puts_after_first
    assert len(events.events) == 2

    selected = ingest(service, b"Same immutable bytes")
    assert selected.status is IngestionStatus.EMITTED
    assert selected.source.revision_id == first.source.revision_id
    assert len(blobs.puts) == puts_after_first
    assert len(events.events) == 3
    assert events.events[-1].event_type == SOURCE_REVISION_SELECTED


def test_repeated_historical_selection_converges_without_duplicate_revisions_or_blobs() -> None:
    blobs = CountingBlobStore()
    events = MemoryEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )

    first_a = ingest(service, b"Revision A")
    first_b = ingest(service, b"Revision B")
    puts_after_revisions = tuple(blobs.puts)
    second_a = ingest(service, b"Revision A")
    second_b = ingest(service, b"Revision B")
    third_a = ingest(service, b"Revision A")
    retry_a = ingest(service, b"Revision A")

    assert second_a.source.revision_id == first_a.source.revision_id
    assert second_b.source.revision_id == first_b.source.revision_id
    assert third_a.source.revision_id == first_a.source.revision_id
    assert retry_a.status is IngestionStatus.IDEMPOTENT
    assert tuple(blobs.puts) == puts_after_revisions
    assert [event.event_type for event in events.events] == [
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SELECTED,
        SOURCE_REVISION_SELECTED,
        SOURCE_REVISION_SELECTED,
    ]
    assert len({event.event_id for event in events.events}) == 5


class LostSelectionOutputEventStore(MemoryEventStore):
    fail_next_append = False

    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: Sequence[DomainEvent],
    ) -> int:
        committed = super().append(course_id, expected_sequence, events)
        if self.fail_next_append:
            self.fail_next_append = False
            raise EventSequenceConflictError(course_id, expected_sequence, committed)
        return committed


def test_lost_selection_output_reconciles_and_explicit_cas_remains_retryable() -> None:
    blobs = CountingBlobStore()
    events = LostSelectionOutputEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )
    first = ingest(service, b"Revision A")
    ingest(service, b"Revision B")
    puts_before = tuple(blobs.puts)

    events.fail_next_append = True
    reconciled = ingest(service, b"Revision A")

    assert reconciled.status is IngestionStatus.IDEMPOTENT
    assert reconciled.source.revision_id == first.source.revision_id
    assert reconciled.committed_sequence == 3
    assert tuple(blobs.puts) == puts_before

    ingest(service, b"Revision B")
    with pytest.raises(TextIngestionError) as caught:
        service.ingest(
            filename="cardiology.md",
            content=b"Revision A",
            source_id=SourceId("source-cardiology"),
            title="Cardiology notes",
            trust_level=90,
            source_role="primary",
            context=context(),
            expected_sequence=3,
        )
    assert caught.value.code is IngestionErrorCode.SEQUENCE_CONFLICT
    assert caught.value.retryable


def test_alternate_chunk_size_changes_revision_and_is_persisted_exactly() -> None:
    blobs = CountingBlobStore()
    events = MemoryEventStore()
    first_service = TextIngestionService(
        blobs=blobs,
        events=events,
        clock=FixedClock(),
        courses=ExistingCourseView(),
        chunking=ChunkingConfig(max_characters=20),
    )
    second_service = TextIngestionService(
        blobs=blobs,
        events=events,
        clock=FixedClock(),
        courses=ExistingCourseView(),
        chunking=ChunkingConfig(max_characters=10),
    )

    first = ingest(first_service, b"Same bytes with several words")
    second = ingest(second_service, b"Same bytes with several words")

    assert first.source.revision_id != second.source.revision_id
    decoded_first = decode_source_revision_ingested(events.events[0].payload)
    decoded_second = decode_source_revision_ingested(events.events[1].payload)
    assert (decoded_first.chunking.version, decoded_first.chunking.max_characters) == (
        CHUNKER_VERSION,
        20,
    )
    assert (decoded_second.chunking.version, decoded_second.chunking.max_characters) == (
        CHUNKER_VERSION,
        10,
    )


def test_unsupported_chunker_version_has_zero_filesystem_or_sqlite_effects(
    tmp_path: Path,
) -> None:
    _, blobs, events = make_service(tmp_path)
    unsupported = TextIngestionService(
        blobs=blobs,
        events=events,
        clock=FixedClock(),
        courses=create_canonical_course(events, context().course_id),
        chunking=ChunkingConfig(version="future-chunker"),
    )

    with pytest.raises(TextIngestionError) as caught:
        ingest(unsupported, b"Content must not be written")

    assert caught.value.code is IngestionErrorCode.UNSUPPORTED_CONFIGURATION
    assert not caught.value.retryable
    assert len(events.read(context().course_id)) == 1
    objects = tmp_path / "blobs" / "objects"
    assert not any(path.is_file() for path in objects.rglob("*"))
    blobs.close()


def test_concurrent_identical_revision_returns_idempotent_after_exact_conflict() -> None:
    blobs = CountingBlobStore()
    events = ConcurrentIdenticalEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )

    result = ingest(service, b"Concurrently ingested")

    assert result.status is IngestionStatus.IDEMPOTENT
    assert result.committed_sequence == 1
    assert len(events.events) == 1


def test_expected_sequence_revalidates_before_idempotency_or_blob_writes() -> None:
    blobs = CountingBlobStore()
    events = MemoryEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )
    first = service.ingest(
        filename="notes.txt",
        content=b"CAS content",
        source_id=SourceId("source-1"),
        title="Notes",
        trust_level=50,
        source_role="primary",
        context=context(),
        expected_sequence=0,
    )
    puts_after_first = tuple(blobs.puts)

    with pytest.raises(TextIngestionError) as caught:
        service.ingest(
            filename="notes.txt",
            content=b"CAS content",
            source_id=SourceId("source-1"),
            title="Notes",
            trust_level=50,
            source_role="primary",
            context=context(),
            expected_sequence=0,
        )

    assert first.status is IngestionStatus.EMITTED
    assert caught.value.code is IngestionErrorCode.SEQUENCE_CONFLICT
    assert caught.value.retryable
    assert tuple(blobs.puts) == puts_after_first
    assert len(events.events) == 1


def test_expected_sequence_does_not_reconcile_a_concurrent_winner() -> None:
    blobs = CountingBlobStore()
    events = ConcurrentIdenticalEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )

    with pytest.raises(TextIngestionError) as caught:
        service.ingest(
            filename="notes.txt",
            content=b"Concurrent CAS content",
            source_id=SourceId("source-1"),
            title="Notes",
            trust_level=50,
            source_role="primary",
            context=context(),
            expected_sequence=0,
        )

    assert caught.value.code is IngestionErrorCode.SEQUENCE_CONFLICT
    assert caught.value.retryable
    assert len(events.events) == 1


def test_unchanged_legacy_revision_is_current_without_v2_duplicate() -> None:
    blobs = CountingBlobStore()
    events = MemoryEventStore()
    service = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=ExistingCourseView()
    )
    first = ingest(service, b"Legacy identity content")
    legacy_id = legacy_revision_id_for(
        original_sha256=first.source.checksum_sha256,
        source_id=first.source.source_id,
        kind=first.source.kind,
        normalization_version=first.source.normalization_version,
        chunker_version=CHUNKER_VERSION,
        max_characters=ChunkingConfig().max_characters,
    )
    legacy_source = replace(first.source, revision_id=legacy_id)
    legacy_chunks = chunk_text(
        b"Legacy identity content".decode(),
        source_id=legacy_source.source_id,
        revision_id=legacy_id,
        kind=legacy_source.kind,
        config=ChunkingConfig(),
    )
    events.events = [
        DomainEvent(
            source_event_id_for(context().course_id, legacy_id),
            context().course_id,
            1,
            SOURCE_REVISION_INGESTED,
            SOURCE_REVISION_SCHEMA_VERSION,
            Actor(context().principal_kind, context().principal_id),
            legacy_source.created_at,
            context().correlation_id,
            source_revision_payload(legacy_source, legacy_chunks),
        )
    ]
    puts_before = tuple(blobs.puts)

    result = ingest(service, b"Legacy identity content")

    assert result.status is IngestionStatus.IDEMPOTENT
    assert result.source.revision_id == legacy_id
    assert tuple(blobs.puts) == puts_before
    assert len(events.events) == 1

    changed = ingest(service, b"Changed after legacy")
    selected = ingest(service, b"Legacy identity content")

    assert changed.source.revision_id != legacy_id
    assert selected.status is IngestionStatus.EMITTED
    assert selected.source.revision_id == legacy_id
    assert events.events[-1].event_type == SOURCE_REVISION_SELECTED
    assert len(events.events) == 3
