from __future__ import annotations

import sqlite3
from contextlib import closing
from hashlib import sha256
from pathlib import Path

import pytest

from cardine.courses import register_course_events
from cardine.evaluation.snapshot import audit_snapshot
from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore, SQLiteFtsRetrieval
from study_agent.adapters.sqlite.fts_retrieval import RetrievalIndexIntegrityError
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SourceId
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.ports.retrieval import RetrievalQuery
from study_agent.retrieval import CourseSourceContent
from study_agent.state import EventRegistry
from tests.course_fixtures import create_canonical_course
from tests.integration.test_fts_retrieval import FixedClock


def test_snapshot_reads_committed_wal_and_never_repairs_live_index(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    course_id = CourseId("course-1")
    with (
        FilesystemBlobStore(tmp_path / "blobs") as blobs,
        closing(sqlite3.connect(state / "events.sqlite3")) as event_wal,
        closing(sqlite3.connect(state / "retrieval.sqlite3")) as retrieval_wal,
    ):
        for connection in (event_wal, retrieval_wal):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA wal_autocheckpoint=0")
        registry = EventRegistry()
        register_course_events(registry)
        register_source_revision_events(registry, blobs.get)
        events = SQLiteEventStore(state / "events.sqlite3", registry)
        event_wal.execute("SELECT count(*) FROM events").fetchone()
        courses = create_canonical_course(events, course_id)
        ingestion = TextIngestionService(
            blobs=blobs, events=events, clock=FixedClock(), courses=courses,
        )
        ingestion.ingest(
            filename="enzyme.txt", content=b"The nucleophile attacks the substrate.",
            source_id=SourceId("enzyme"), title="Enzyme notes", trust_level=90,
            source_role="primary", context=ExecutionContext(
                PrincipalKind.SERVICE, "ingestion", course_id, CorrelationId("audit"),
            ),
        )
        content = CourseSourceContent(course_id, events, blobs)
        retrieval = SQLiteFtsRetrieval(state / "retrieval.sqlite3", content)
        retrieval_wal.execute("SELECT count(*) FROM retrieval_documents").fetchone()
        retrieval.index(content.documents())
        before = {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in state.iterdir() if not path.name.endswith("-shm")
        }
        blobs_before = {
            path: sha256(path.read_bytes()).hexdigest()
            for path in (tmp_path / "blobs").rglob("*") if path.is_file()
        }
        assert (state / "events.sqlite3-wal").stat().st_size > 0
        with audit_snapshot(tmp_path, course_id) as (catalog, retrieval, _, metadata):
            result = retrieval.search(RetrievalQuery(course_id, "nucleophile substrate"))
            assert len(result.evidence) == 1
            assert catalog.resolve(result.evidence[0].citation).text == result.evidence[0].text
            assert metadata["indexed_chunks"] == 1
        assert before == {
            path.name: sha256(path.read_bytes()).hexdigest()
            for path in state.iterdir() if not path.name.endswith("-shm")
        }
        assert blobs_before == {
            path: sha256(path.read_bytes()).hexdigest()
            for path in (tmp_path / "blobs").rglob("*") if path.is_file()
        }
        retrieval_wal.execute("UPDATE retrieval_fts SET text='tampered bytes'")
        retrieval_wal.commit()
        with pytest.raises(RetrievalIndexIntegrityError), audit_snapshot(tmp_path, course_id):
            pass
        assert retrieval_wal.execute("SELECT text FROM retrieval_fts").fetchone() == (
            "tampered bytes",
        )
