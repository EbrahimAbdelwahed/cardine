"""Read-only canonical repository observation for lifecycle planning."""

from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path

from study_agent.adapters.filesystem.blob_store import FilesystemBlobStore
from study_agent.adapters.filesystem.repository_target import RepositoryObservationHandle
from study_agent.artifacts import register_artifact_events
from study_agent.assessments import register_assessment_events
from cardine.courses import ProjectionCourseView, register_course_events
from study_agent.domain import ChunkId, Citation, CourseId, ResolvedCitation
from study_agent.ingestion import register_source_revision_events
from study_agent.lifecycle import (
    IndexObservationState,
    ObservedCourse,
    ObservedIndex,
    ObservedSource,
    RepositoryObservation,
    RepositoryObservationState,
)
from study_agent.ports.retrieval import RetrievalDocument
from study_agent.recall import register_recall_events
from study_agent.repository_config import LocalRepositoryConfig
from study_agent.retrieval import CourseSourceContent
from study_agent.sessions import register_session_events
from study_agent.state import EventRegistry, replay
from study_agent.study_context import register_study_context_events

from .event_store import SQLiteEventStore
from .fts_retrieval import SQLiteFtsRetrieval


class _CanonicalCatalog:
    """Repository-wide retrieval catalog derived only from canonical streams."""

    def __init__(
        self,
        course_ids: tuple[CourseId, ...],
        content_for: Callable[[CourseId], CourseSourceContent],
    ) -> None:
        self._course_ids = course_ids
        self._content_for = content_for

    def documents(
        self, *, include_superseded: bool = False
    ) -> tuple[RetrievalDocument, ...]:
        return tuple(
            document
            for course_id in self._course_ids
            for document in self._content_for(course_id).documents(
                include_superseded=include_superseded
            )
        )

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument:
        matches = tuple(
            document
            for document in self.documents(include_superseded=True)
            if document.chunk.chunk_id == chunk_id
        )
        if len(matches) != 1:
            raise LookupError("canonical chunk was not found uniquely")
        return matches[0]

    def resolve(self, citation: Citation) -> ResolvedCitation:
        document = self.canonical_document(citation.chunk_id)
        return self._content_for(document.course_id).resolve(citation)


def observe_local_repository(
    handle: RepositoryObservationHandle, config: LocalRepositoryConfig
) -> RepositoryObservation:
    """Replay canonical state and audit derived retrieval state without writes."""

    if not isinstance(handle, RepositoryObservationHandle):
        raise TypeError("handle must be a RepositoryObservationHandle")
    if not isinstance(config, LocalRepositoryConfig):
        raise TypeError("config must be LocalRepositoryConfig")

    try:
        handle.verify_binding()
        blob_descriptor = handle.directory_descriptor("blobs")
        try:
            blobs = FilesystemBlobStore.from_descriptor(blob_descriptor)
        finally:
            os.close(blob_descriptor)
        with blobs:
            events_path = handle.database_descriptor_path("events")
            if events_path is None:
                courses: tuple[ObservedCourse, ...] = ()
                catalog = _CanonicalCatalog((), _unavailable_content)
            else:
                registry = EventRegistry()
                register_course_events(registry)
                register_source_revision_events(registry, blobs.get)
                register_session_events(registry)
                register_study_context_events(registry)
                register_artifact_events(registry)
                register_assessment_events(registry)
                register_recall_events(registry)
                events = SQLiteEventStore(events_path, registry, read_only=True)
                course_ids = events.list_course_ids()
                projections = {
                    course_id: replay(course_id, events.read(course_id), registry)
                    for course_id in course_ids
                }
                view = ProjectionCourseView(projections.__getitem__)

                def content_for(course_id: CourseId) -> CourseSourceContent:
                    return CourseSourceContent(course_id, events, blobs)

                courses = tuple(
                    _observed_course(course_id, projections[course_id].sequence, view, content_for)
                    for course_id in course_ids
                )
                catalog = _CanonicalCatalog(course_ids, content_for)
            index = _observe_index(
                handle.database_descriptor_path("retrieval"), catalog
            )
        handle.verify_binding()
    except (LookupError, OSError, RuntimeError, ValueError, sqlite3.DatabaseError):
        return RepositoryObservation(RepositoryObservationState.CONFLICT, config)

    return RepositoryObservation(
        RepositoryObservationState.COMPATIBLE,
        config,
        courses,
        index,
    )


def _unavailable_content(course_id: CourseId) -> CourseSourceContent:
    raise LookupError(f"course {course_id} is unavailable")


def _observed_course(
    course_id: CourseId,
    sequence: int,
    view: ProjectionCourseView,
    content_for: Callable[[CourseId], CourseSourceContent],
) -> ObservedCourse:
    current = tuple(
        record
        for record in content_for(course_id).catalog()
        if record.is_current_revision
    )
    sources = tuple(
        ObservedSource(
            str(record.source.source_id),
            str(record.source.revision_id),
            record.source.kind,
            record.source.title,
            record.source.trust_level,
            record.source.source_role,
            record.source.checksum_sha256,
            record.source.byte_length,
        )
        for record in current
    )
    return ObservedCourse(view.get(course_id), sequence, sources)


def _observe_index(
    database: Path | None, catalog: _CanonicalCatalog
) -> ObservedIndex:
    if database is None:
        return ObservedIndex(IndexObservationState.MISSING)
    try:
        SQLiteFtsRetrieval(database, catalog, read_only=True).audit()
    except (LookupError, OSError, RuntimeError, ValueError, sqlite3.DatabaseError):
        return ObservedIndex(IndexObservationState.STALE)
    return ObservedIndex(IndexObservationState.HEALTHY)


__all__ = ["observe_local_repository"]
