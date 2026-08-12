from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore
from cardine.courses import register_course_events
from study_agent.domain import (
    Citation,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SourceId,
)
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.retrieval import (
    CourseSourceContent,
    SourceContentError,
    SourceContentErrorCode,
)
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 11, 13, tzinfo=UTC)


def test_real_ingestion_resolves_canonical_unicode_and_detects_blob_corruption(
    tmp_path: Path,
) -> None:
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "ingestion",
        CourseId("course-1"),
        CorrelationId("correlation-1"),
    )
    courses = create_canonical_course(events, context.course_id)
    service = TextIngestionService(
        blobs=blobs, events=events, clock=Clock(), courses=courses
    )
    result = service.ingest(
        filename="cardiology.md",
        content="# Heart\r\n\r\nCafe\u0301 🫀 valve.".encode(),
        source_id=SourceId("source-heart"),
        title="Cardiology",
        trust_level=95,
        source_role="primary",
        context=context,
    )
    content = CourseSourceContent(context.course_id, events, blobs)
    assert content.get_text(result.source.revision_id) == "# Heart\n\nCafé 🫀 valve."
    document = content.documents()[1]
    citation = Citation(
        document.source_id,
        document.revision_id,
        document.chunk.chunk_id,
        document.chunk.start_offset,
        document.chunk.end_offset,
        "untrusted locator",
        document.text,
    )
    assert content.resolve(citation).text == "Café 🫀 valve."

    digest = result.source.normalized_blob.checksum_sha256
    target = tmp_path / "blobs" / "objects" / digest[:2] / digest[2:4] / digest
    target.chmod(0o644)
    target.write_bytes(b"x" * result.source.normalized_blob.byte_length)
    with pytest.raises(SourceContentError) as corrupted:
        content.get_text(result.source.revision_id)
    assert corrupted.value.code is SourceContentErrorCode.INTEGRITY_ERROR
    blobs.close()
