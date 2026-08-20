"""Event- and blob-backed canonical source content resolution."""

from __future__ import annotations

from dataclasses import dataclass

from study_agent.domain.identifiers import ChunkId, CourseId, RevisionId, SourceId
from study_agent.domain.source import (
    Citation,
    ResolvedCitation,
    SourceChunk,
    SourceDocument,
)
from study_agent.ingestion import (
    GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_SELECTED,
    SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
    SourceRevisionIngested,
    decode_source_revision_event,
    decode_source_revision_selected_event,
)
from study_agent.ingestion.projection import validate_generated_source_admission
from study_agent.ports.retrieval import RetrievalDocument
from study_agent.ports.storage import BlobStore, EventStore
from study_agent.state import Projection

from .errors import SourceContentError, SourceContentErrorCode


@dataclass(frozen=True, slots=True)
class SourceRevisionRecord:
    course_id: CourseId
    source: SourceDocument
    chunks: tuple[SourceChunk, ...]
    text: str
    is_current_revision: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunks", tuple(self.chunks))


def canonical_source_locator(
    record: SourceRevisionRecord,
    chunk: SourceChunk,
    start: int,
    end: int,
) -> str:
    """Build the canonical human-readable locator for a source span."""
    section = " > ".join(chunk.section_path) or f"chunk {chunk.ordinal + 1}"
    page_label = ""
    provenance = record.source.conversion_provenance
    if provenance is not None and provenance.page_spans:
        pages = tuple(
            span.page
            for span in provenance.page_spans
            if start < span.end_offset and end > span.start_offset
        )
        if pages:
            page_label = (
                f" · page {pages[0]}"
                if len(pages) == 1
                else f" · pages {pages[0]}-{pages[-1]}"
            )
    return f"{record.source.title} · {section}{page_label} · chars {start}-{end}"


class CourseSourceContent:
    """Read-only canonical source adapter scoped to one course event stream."""

    def __init__(self, course_id: CourseId, events: EventStore, blobs: BlobStore) -> None:
        self._course_id = course_id
        self._events = events
        self._blobs = blobs

    def _decode(
        self,
    ) -> tuple[
        tuple[tuple[SourceRevisionIngested, str], ...],
        dict[SourceId, RevisionId],
    ]:
        decoded: list[tuple[SourceRevisionIngested, str]] = []
        seen: dict[tuple[SourceId, RevisionId], SourceRevisionIngested] = {}
        current: dict[SourceId, RevisionId] = {}
        stream = tuple(self._events.read(self._course_id))
        has_generated = any(
            event.event_type == SOURCE_REVISION_INGESTED
            and event.schema_version == GENERATED_SOURCE_REVISION_SCHEMA_VERSION
            for event in stream
        )
        projection: Projection | None = None
        if has_generated:
            load_projection = getattr(self._events, "projection", None)
            if not callable(load_projection):
                raise SourceContentError(
                    SourceContentErrorCode.INTEGRITY_ERROR,
                    "generated source content requires a canonical projection",
                )
            projection = load_projection(self._course_id)
            expected_sequence = stream[-1].course_sequence if stream else 0
            if (
                not isinstance(projection, Projection)
                or projection.course_id != self._course_id
                or projection.sequence != expected_sequence
            ):
                raise SourceContentError(
                    SourceContentErrorCode.INTEGRITY_ERROR,
                    "generated source projection is missing or stale",
                )
        for event in stream:
            if (
                event.event_type == SOURCE_REVISION_SELECTED
                and event.schema_version == SOURCE_REVISION_SELECTED_SCHEMA_VERSION
            ):
                try:
                    selection = decode_source_revision_selected_event(event)
                    if (selection.source_id, selection.revision_id) not in seen:
                        raise ValueError(
                            "selected revision does not exist in source history"
                        )
                except ValueError as error:
                    raise SourceContentError(
                        SourceContentErrorCode.INTEGRITY_ERROR,
                        "source selection event failed integrity validation",
                    ) from error
                current[selection.source_id] = selection.revision_id
                continue
            if (
                event.event_type != SOURCE_REVISION_INGESTED
                or event.schema_version
                not in (SOURCE_REVISION_SCHEMA_VERSION, GENERATED_SOURCE_REVISION_SCHEMA_VERSION)
            ):
                continue
            try:
                revision = decode_source_revision_event(event, self._blobs.get)
                if event.schema_version == GENERATED_SOURCE_REVISION_SCHEMA_VERSION:
                    assert projection is not None
                    validate_generated_source_admission(projection.state, event, revision)
                normalized = self._blobs.get(revision.source.normalized_blob)
                text = normalized.decode("utf-8", errors="strict")
            except LookupError as error:
                raise SourceContentError(
                    SourceContentErrorCode.NOT_FOUND,
                    "source content blob is missing",
                ) from error
            except (OSError, UnicodeError, ValueError) as error:
                raise SourceContentError(
                    SourceContentErrorCode.INTEGRITY_ERROR,
                    "source event or content failed integrity validation",
                ) from error
            key = (revision.source.source_id, revision.source.revision_id)
            existing = seen.get(key)
            if existing is not None:
                if existing != revision:
                    raise SourceContentError(
                        SourceContentErrorCode.INTEGRITY_ERROR,
                        "revision identity has conflicting immutable manifests",
                    )
                continue
            seen[key] = revision
            decoded.append((revision, text))
            current[revision.source.source_id] = revision.source.revision_id
        return tuple(decoded), current

    def catalog(self) -> tuple[SourceRevisionRecord, ...]:
        decoded, current = self._decode()
        return tuple(
            SourceRevisionRecord(
                self._course_id,
                revision.source,
                revision.chunks,
                text,
                current[revision.source.source_id] == revision.source.revision_id,
            )
            for revision, text in decoded
        )

    def documents(self, *, include_superseded: bool = False) -> tuple[RetrievalDocument, ...]:
        documents: list[RetrievalDocument] = []
        for record in self.catalog():
            if not include_superseded and not record.is_current_revision:
                continue
            for chunk in record.chunks:
                documents.append(
                    RetrievalDocument(
                        self._course_id,
                        record.source.source_id,
                        record.source.revision_id,
                        chunk,
                        record.text[chunk.start_offset : chunk.end_offset],
                        record.source.title,
                        record.source.kind,
                        record.source.source_role,
                        record.source.trust_level,
                        record.is_current_revision,
                    )
                )
        return tuple(documents)

    def _record(self, revision_id: RevisionId) -> SourceRevisionRecord:
        for record in self.catalog():
            if record.source.revision_id == revision_id:
                return record
        raise SourceContentError(
            SourceContentErrorCode.NOT_FOUND,
            f"source revision {revision_id} was not found in course {self._course_id}",
        )

    def get_text(self, revision_id: RevisionId) -> str:
        return self._record(revision_id).text

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument:
        for document in self.documents(include_superseded=True):
            if document.chunk.chunk_id == chunk_id:
                return document
        raise SourceContentError(
            SourceContentErrorCode.NOT_FOUND,
            f"source chunk {chunk_id} was not found in course {self._course_id}",
        )

    def resolve(self, citation: Citation) -> ResolvedCitation:
        record = self._record(citation.revision_id)
        if record.source.source_id != citation.source_id:
            raise SourceContentError(
                SourceContentErrorCode.OWNERSHIP_MISMATCH,
                "citation source does not own the declared revision",
            )
        chunk = next(
            (item for item in record.chunks if item.chunk_id == citation.chunk_id),
            None,
        )
        if chunk is None:
            raise SourceContentError(
                SourceContentErrorCode.OWNERSHIP_MISMATCH,
                "citation chunk does not belong to the declared revision",
            )
        if (
            citation.start_offset < chunk.start_offset
            or citation.end_offset > chunk.end_offset
        ):
            raise SourceContentError(
                SourceContentErrorCode.OUT_OF_BOUNDS,
                "citation span must lie entirely inside its declared chunk",
            )
        text = record.text[citation.start_offset : citation.end_offset]
        if citation.quoted_snippet is not None and citation.quoted_snippet != text:
            raise SourceContentError(
                SourceContentErrorCode.QUOTE_MISMATCH,
                "citation quote does not match canonical normalized text",
            )
        canonical = Citation(
            citation.source_id,
            citation.revision_id,
            citation.chunk_id,
            citation.start_offset,
            citation.end_offset,
            canonical_source_locator(
                record, chunk, citation.start_offset, citation.end_offset
            ),
            text,
        )
        return ResolvedCitation(canonical, text)
