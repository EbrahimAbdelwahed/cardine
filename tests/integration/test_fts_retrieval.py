from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore, SQLiteFtsRetrieval
from cardine.courses import register_course_events
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SourceId,
)
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.ports.retrieval import RetrievalQuery
from study_agent.retrieval import CourseSourceContent
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course


class FixedClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 11, 12, 0, tzinfo=UTC)


def test_ingestion_to_fts_search_and_rebuild_preserves_canonical_results(
    tmp_path: Path,
) -> None:
    course_id = CourseId("course-1")
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "ingestion",
        course_id,
        CorrelationId("correlation-1"),
    )
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_course_events(registry)
    register_source_revision_events(registry, blobs.get)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    courses = create_canonical_course(events, course_id)
    ingestion = TextIngestionService(
        blobs=blobs, events=events, clock=FixedClock(), courses=courses
    )

    ingestion.ingest(
        filename="heart.txt",
        content=b"The old cardiac valve description.",
        source_id=SourceId("heart"),
        title="Heart notes",
        trust_level=90,
        source_role="primary",
        context=context,
    )
    ingestion.ingest(
        filename="heart.txt",
        content=b"The current cardiac valve controls forward flow.",
        source_id=SourceId("heart"),
        title="Heart notes",
        trust_level=90,
        source_role="primary",
        context=context,
    )
    ingestion.ingest(
        filename="kidney.md",
        content=b"# Kidney\n\nGlomerular filtration creates ultrafiltrate.",
        source_id=SourceId("kidney"),
        title="Kidney notes",
        trust_level=80,
        source_role="primary",
        context=context,
    )

    canonical = CourseSourceContent(course_id, events, blobs)
    documents = canonical.documents(include_superseded=True)
    retrieval = SQLiteFtsRetrieval(tmp_path / "fts.sqlite3", canonical)
    first_receipt = retrieval.index(documents)
    second_receipt = retrieval.index(documents)

    current = retrieval.search(RetrievalQuery(course_id, "cardiac valve"))
    historical = retrieval.search(
        RetrievalQuery(course_id, "cardiac valve", include_superseded=True)
    )
    before_rebuild = retrieval.search(RetrievalQuery(course_id, "glomerular filtration"))
    retrieval.rebuild(documents)
    after_rebuild = retrieval.search(RetrievalQuery(course_id, "glomerular filtration"))

    assert first_receipt.indexed_chunks == second_receipt.indexed_chunks == len(documents)
    assert len(current.evidence) == 1
    assert "current cardiac valve" in current.evidence[0].text
    assert len(historical.evidence) == 2
    assert before_rebuild == after_rebuild
    for evidence in (*current.evidence, *before_rebuild.evidence):
        assert canonical.resolve(evidence.citation).text == evidence.text
    assert events.verify_projection(course_id)
    blobs.close()
