"""Read-only SQLite backups with canonical event/blob validation for offline audits."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

from cardine.courses import ProjectionCourseView, register_course_events
from cardine.domain.course import CourseProfile
from cardine.integrations.study_agent.course_policy import (
    ProjectionSourceLifetimeView,
    register_course_policy_events,
)
from study_agent.adapters.filesystem.blob_store import FilesystemBlobStore
from study_agent.adapters.sqlite.event_store import ProjectionConsistencyError, SQLiteEventStore
from study_agent.adapters.sqlite.fts_retrieval import SQLiteFtsRetrieval
from study_agent.artifacts import register_artifact_events
from study_agent.assessments import register_assessment_events
from study_agent.domain import ChunkId, Citation, CourseId, ResolvedCitation
from study_agent.ingestion import register_source_revision_events
from study_agent.ports.retrieval import RetrievalDocument
from study_agent.recall import register_recall_events
from study_agent.retrieval import CourseSourceContent
from study_agent.retrieval.content import canonical_source_locator
from study_agent.sessions import register_session_events
from study_agent.state import EventRegistry
from study_agent.study_context import register_study_context_events


class AuditCatalog:
    """Frozen verified catalog; memoized resolutions are valid only for this snapshot."""

    def __init__(
        self, contents: dict[CourseId, CourseSourceContent],
        lifetime: ProjectionSourceLifetimeView,
    ) -> None:
        retired = {
            course_id: lifetime.retired_source_ids(course_id) for course_id in contents
        }
        self._records = {
            record.source.revision_id: record
            for content in contents.values() for record in content.catalog()
        }
        self._documents = tuple(
            item for course_id, content in contents.items()
            for item in content.documents(include_superseded=True)
            if item.source_id not in retired[course_id]
        )
        self._by_chunk = {item.chunk.chunk_id: item for item in self._documents}
        if len(self._by_chunk) != len(self._documents):
            raise ValueError("canonical snapshot contains duplicate chunk ids")
        self._resolved: dict[Citation, ResolvedCitation] = {}

    def documents(self, *, include_superseded: bool = False) -> tuple[RetrievalDocument, ...]:
        return tuple(
            item for item in self._documents if include_superseded or item.is_current_revision
        )

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument:
        return self._by_chunk[chunk_id]

    def resolve(self, citation: Citation) -> ResolvedCitation:
        if citation not in self._resolved:
            document = self.canonical_document(citation.chunk_id)
            if (
                citation.source_id != document.source_id
                or citation.revision_id != document.revision_id
                or not document.chunk.start_offset <= citation.start_offset < citation.end_offset
                or citation.end_offset > document.chunk.end_offset
            ):
                raise ValueError("audit citation escapes its canonical chunk")
            record = self._records[document.revision_id]
            text = record.text[citation.start_offset:citation.end_offset]
            if citation.quoted_snippet is not None and citation.quoted_snippet != text:
                raise ValueError("audit citation quote differs from canonical bytes")
            self._resolved[citation] = ResolvedCitation(replace(
                citation,
                locator=canonical_source_locator(
                    record, document.chunk, citation.start_offset, citation.end_offset,
                ),
                quoted_snippet=text,
            ), text)
        return self._resolved[citation]


@contextmanager
def audit_snapshot(
    repository: Path, course_id: CourseId,
) -> Iterator[tuple[AuditCatalog, SQLiteFtsRetrieval, CourseProfile, dict[str, object]]]:
    """Include committed WAL data; immutable SQLite reads touch only completed backups.

    The two backups are not an atomic multi-database transaction. The subsequent
    complete integrity audit rejects inconsistent event/index snapshots.
    """
    with TemporaryDirectory(prefix="cardine-retrieval-audit-") as directory:
        snapshot = Path(directory)
        for name in ("events", "retrieval"):
            original = repository / "state" / f"{name}.sqlite3"
            with (
                closing(sqlite3.connect(original.resolve().as_uri() + "?mode=ro", uri=True)) as src,
                closing(sqlite3.connect(snapshot / f"{name}.sqlite3")) as dst,
            ):
                src.backup(dst)
        with FilesystemBlobStore(repository / "blobs", read_only=True) as blobs:
            registry = EventRegistry()
            register_course_events(registry)
            register_course_policy_events(registry)
            register_source_revision_events(registry, blobs.get)
            register_session_events(registry)
            register_study_context_events(registry)
            register_artifact_events(registry)
            register_assessment_events(registry)
            register_recall_events(registry)
            events = SQLiteEventStore(snapshot / "events.sqlite3", registry, read_only=True)
            course_ids = events.list_course_ids()
            for key in course_ids:
                if not events.verify_projection(key):
                    raise ProjectionConsistencyError(
                        "audit projection differs from canonical event replay"
                    )
            catalog = AuditCatalog(
                {key: CourseSourceContent(key, events, blobs) for key in course_ids},
                ProjectionSourceLifetimeView(events.projection),
            )
            retrieval = SQLiteFtsRetrieval(snapshot / "retrieval.sqlite3", catalog, read_only=True)
            receipt = retrieval.audit()
            profile = ProjectionCourseView(events.projection).get(course_id)
            yield catalog, retrieval, profile, {
                "indexed_chunks": receipt.indexed_chunks,
                "index_version": receipt.index_version,
                "catalog_fingerprint": receipt.catalog_fingerprint,
                "course_sequence": events.projection(course_id).sequence,
                "minimum_trust_level": profile.source_policy.minimum_trust_level,
                "allowed_roles": list(profile.source_policy.allowed_roles),
            }
