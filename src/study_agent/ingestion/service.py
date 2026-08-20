"""Application service for immutable text and Markdown source revisions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import PurePath

from study_agent.domain.context import ExecutionContext
from study_agent.domain.events import Actor, DomainEvent
from study_agent.domain.identifiers import RevisionId, SourceId
from study_agent.domain.provenance import (
    ContentOrigin,
    DocumentConversionProvenance,
    StructureOrigin,
)
from study_agent.domain.source import SourceChunk, SourceDocument, SourceKind
from study_agent.ports import BlobStore, ClockPort, CourseViewPort, EventStore
from study_agent.ports.storage import EventSequenceConflictError

from .chunking import CHUNKER_VERSION, DEFAULT_CHUNKING_CONFIG, ChunkingConfig
from .events import (
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_SELECTED,
    SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
    SourceRevisionIngested,
    decode_source_revision_ingested,
    decode_source_revision_selected_event,
    source_revision_selected_payload,
)
from .identity import (
    revision_id_for,
    source_event_id_for,
    source_kind_contract,
    source_revision_selected_event_id_for,
)
from .normalization import InvalidUtf8Error
from .preparation import predicted_blob, prepare_chunks, prepare_text, write_expected_blob
from .projection import source_revision_payload


class IngestionErrorCode(StrEnum):
    UNSUPPORTED_EXTENSION = "unsupported_extension"
    INVALID_UTF8 = "invalid_utf8"
    INVALID_CONTENT = "invalid_content"
    SEQUENCE_CONFLICT = "sequence_conflict"
    BLOB_MISMATCH = "blob_mismatch"
    UNSUPPORTED_CONFIGURATION = "unsupported_configuration"


class IngestionStatus(StrEnum):
    EMITTED = "emitted"
    IDEMPOTENT = "idempotent"


class TextIngestionError(Exception):
    def __init__(self, code: IngestionErrorCode, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class TextIngestionResult:
    status: IngestionStatus
    source: SourceDocument
    chunks: tuple[SourceChunk, ...]
    committed_sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunks", tuple(self.chunks))


class TextIngestionService:
    def __init__(
        self,
        *,
        blobs: BlobStore,
        events: EventStore,
        clock: ClockPort,
        courses: CourseViewPort,
        chunking: ChunkingConfig = DEFAULT_CHUNKING_CONFIG,
    ) -> None:
        self._blobs = blobs
        self._events = events
        self._clock = clock
        self._courses = courses
        self._chunking = chunking

    def ingest(
        self,
        *,
        filename: str,
        content: bytes,
        source_id: SourceId,
        title: str,
        trust_level: int,
        source_role: str,
        context: ExecutionContext,
        expected_sequence: int | None = None,
        content_origin: ContentOrigin = ContentOrigin.ORIGINAL,
        conversion_provenance: DocumentConversionProvenance | None = None,
        original_content: bytes | None = None,
    ) -> TextIngestionResult:
        self._courses.get(context.course_id)
        stream = tuple(self._events.read(context.course_id))
        current_sequence = stream[-1].course_sequence if stream else 0
        if expected_sequence is not None and current_sequence != expected_sequence:
            raise TextIngestionError(
                IngestionErrorCode.SEQUENCE_CONFLICT,
                "course stream does not match expected sequence "
                f"{expected_sequence}; observed {current_sequence}",
                retryable=True,
            )
        if self._chunking.version != CHUNKER_VERSION:
            raise TextIngestionError(
                IngestionErrorCode.UNSUPPORTED_CONFIGURATION,
                f"unsupported chunker version: {self._chunking.version}",
            )
        kind, media_type, method = _file_contract(filename)
        try:
            prepared = prepare_text(content)
        except InvalidUtf8Error as error:
            raise TextIngestionError(IngestionErrorCode.INVALID_UTF8, str(error)) from error

        original_bytes = content if original_content is None else original_content
        original_blob = predicted_blob(original_bytes)
        normalized = prepared.normalized
        normalized_blob = prepared.normalized_blob
        revision_id = revision_id_for(
            original_sha256=original_blob.checksum_sha256,
            source_id=source_id,
            kind=kind,
            title=title,
            trust_level=trust_level,
            source_role=source_role,
            normalization_version=normalized.version,
            chunker_version=self._chunking.version,
            max_characters=self._chunking.max_characters,
        )
        now = self._clock.now()
        try:
            source = SourceDocument(
                source_id,
                revision_id,
                kind,
                title,
                media_type,
                original_blob.checksum_sha256,
                original_blob.byte_length,
                now,
                trust_level,
                source_role,
                original_blob,
                normalized_blob,
                normalized.version,
                len(normalized.text),
                StructureOrigin.MECHANICALLY_EXTRACTED,
                method,
                content_origin,
                conversion_provenance,
            )
            chunks = prepare_chunks(
                normalized.text,
                source_id=source_id,
                revision_id=revision_id,
                kind=kind,
                config=self._chunking,
            )
        except ValueError as error:
            raise TextIngestionError(IngestionErrorCode.INVALID_CONTENT, str(error)) from error

        current = _current_revision(stream, source_id)
        if current is not None and _matches_request(current, source, self._chunking):
            if expected_sequence is not None:
                latest = tuple(self._events.read(context.course_id))
                latest_sequence = latest[-1].course_sequence if latest else 0
                if latest_sequence != expected_sequence:
                    raise TextIngestionError(
                        IngestionErrorCode.SEQUENCE_CONFLICT,
                        "course stream advanced before idempotent return; "
                        f"expected {expected_sequence}, observed {latest_sequence}",
                        retryable=True,
                    )
            return TextIngestionResult(
                IngestionStatus.IDEMPOTENT,
                current.source,
                current.chunks,
                current_sequence,
            )
        historical = _find_matching_revision(stream, source_id, source, self._chunking)
        if historical is not None:
            return self._select_historical_revision(
                historical,
                current_sequence=current_sequence,
                context=context,
                expected_sequence=expected_sequence,
                occurred_at=now,
            )

        try:
            payload = source_revision_payload(
                source,
                chunks,
                chunker_version=self._chunking.version,
                max_characters=self._chunking.max_characters,
            )
            decoded = decode_source_revision_ingested(payload)
            if decoded.source != source or decoded.chunks != chunks:
                raise ValueError("typed event payload changed immutable source data")
            if decoded.normalized_character_length != len(normalized.text):
                raise ValueError("typed event payload changed normalized length")
            if (
                decoded.chunking.version != self._chunking.version
                or decoded.chunking.max_characters != self._chunking.max_characters
            ):
                raise ValueError("typed event payload changed chunking configuration")
            event = DomainEvent(
                source_event_id_for(context.course_id, revision_id),
                context.course_id,
                current_sequence + 1,
                SOURCE_REVISION_INGESTED,
                SOURCE_REVISION_SCHEMA_VERSION,
                Actor(context.principal_kind, context.principal_id),
                now,
                context.correlation_id,
                payload,
                session_id=context.session_id,
            )
        except ValueError as error:
            raise TextIngestionError(IngestionErrorCode.INVALID_CONTENT, str(error)) from error

        if current is None or current.source.blob != original_blob:
            try:
                write_expected_blob(self._blobs, original_bytes, original_blob)
            except (TypeError, ValueError) as error:
                raise TextIngestionError(IngestionErrorCode.BLOB_MISMATCH, str(error)) from error
        if current is None or current.source.normalized_blob != normalized_blob:
            try:
                write_expected_blob(self._blobs, normalized.content, normalized_blob)
            except (TypeError, ValueError) as error:
                raise TextIngestionError(IngestionErrorCode.BLOB_MISMATCH, str(error)) from error
        try:
            committed = self._events.append(context.course_id, current_sequence, (event,))
        except EventSequenceConflictError as error:
            concurrent_stream = tuple(self._events.read(context.course_id))
            concurrent = _current_revision(concurrent_stream, source_id)
            if (
                expected_sequence is None
                and concurrent is not None
                and _matches_request(concurrent, source, self._chunking)
            ):
                concurrent_sequence = (
                    concurrent_stream[-1].course_sequence if concurrent_stream else 0
                )
                return TextIngestionResult(
                    IngestionStatus.IDEMPOTENT,
                    concurrent.source,
                    concurrent.chunks,
                    concurrent_sequence,
                )
            raise TextIngestionError(
                IngestionErrorCode.SEQUENCE_CONFLICT,
                "course event sequence changed during ingestion",
                retryable=True,
            ) from error
        return TextIngestionResult(IngestionStatus.EMITTED, source, chunks, committed)

    def _select_historical_revision(
        self,
        revision: SourceRevisionIngested,
        *,
        current_sequence: int,
        context: ExecutionContext,
        expected_sequence: int | None,
        occurred_at: datetime,
    ) -> TextIngestionResult:
        # `occurred_at` is supplied by the service clock in `ingest`; keeping the
        # transition here ensures historical selection never touches blob storage.
        next_sequence = current_sequence + 1
        try:
            event = DomainEvent(
                source_revision_selected_event_id_for(
                    context.course_id,
                    revision.source.source_id,
                    revision.source.revision_id,
                    next_sequence,
                ),
                context.course_id,
                next_sequence,
                SOURCE_REVISION_SELECTED,
                SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
                Actor(context.principal_kind, context.principal_id),
                occurred_at,
                context.correlation_id,
                source_revision_selected_payload(
                    revision.source.source_id, revision.source.revision_id
                ),
                session_id=context.session_id,
            )
            decode_source_revision_selected_event(event)
        except ValueError as error:
            raise TextIngestionError(IngestionErrorCode.INVALID_CONTENT, str(error)) from error
        try:
            committed = self._events.append(context.course_id, current_sequence, (event,))
        except EventSequenceConflictError as error:
            concurrent_stream = tuple(self._events.read(context.course_id))
            concurrent = _current_revision(concurrent_stream, revision.source.source_id)
            if (
                expected_sequence is None
                and concurrent is not None
                and concurrent.source.revision_id == revision.source.revision_id
            ):
                concurrent_sequence = (
                    concurrent_stream[-1].course_sequence if concurrent_stream else 0
                )
                return TextIngestionResult(
                    IngestionStatus.IDEMPOTENT,
                    concurrent.source,
                    concurrent.chunks,
                    concurrent_sequence,
                )
            raise TextIngestionError(
                IngestionErrorCode.SEQUENCE_CONFLICT,
                "course event sequence changed during revision selection",
                retryable=True,
            ) from error
        return TextIngestionResult(
            IngestionStatus.EMITTED, revision.source, revision.chunks, committed
        )


def _file_contract(filename: str) -> tuple[SourceKind, str, str]:
    suffix = PurePath(filename).suffix.lower()
    if suffix == ".txt":
        media_type, method = source_kind_contract(SourceKind.TEXT)
        return SourceKind.TEXT, media_type, method
    if suffix == ".md":
        media_type, method = source_kind_contract(SourceKind.MARKDOWN)
        return SourceKind.MARKDOWN, media_type, method
    raise TextIngestionError(
        IngestionErrorCode.UNSUPPORTED_EXTENSION,
        "only .txt and .md files are supported",
    )


def _find_matching_revision(
    events: tuple[DomainEvent, ...],
    source_id: SourceId,
    requested: SourceDocument,
    chunking: ChunkingConfig,
) -> SourceRevisionIngested | None:
    for event in events:
        if (
            event.event_type != SOURCE_REVISION_INGESTED
            or event.schema_version != SOURCE_REVISION_SCHEMA_VERSION
        ):
            continue
        decoded = decode_source_revision_ingested(event.payload)
        if decoded.source.source_id == source_id and _matches_request(decoded, requested, chunking):
            return decoded
    return None


def _current_revision(
    events: tuple[DomainEvent, ...], source_id: SourceId
) -> SourceRevisionIngested | None:
    revisions: dict[RevisionId, SourceRevisionIngested] = {}
    current_revision_id: RevisionId | None = None
    for event in events:
        if (
            event.event_type == SOURCE_REVISION_INGESTED
            and event.schema_version == SOURCE_REVISION_SCHEMA_VERSION
        ):
            decoded = decode_source_revision_ingested(event.payload)
            if decoded.source.source_id == source_id:
                revisions[decoded.source.revision_id] = decoded
                current_revision_id = decoded.source.revision_id
        elif (
            event.event_type == SOURCE_REVISION_SELECTED
            and event.schema_version == SOURCE_REVISION_SELECTED_SCHEMA_VERSION
        ):
            selected = decode_source_revision_selected_event(event)
            if selected.source_id != source_id:
                continue
            if selected.revision_id not in revisions:
                raise ValueError("selected revision does not exist in source history")
            current_revision_id = selected.revision_id
    if current_revision_id is None:
        return None
    return revisions[current_revision_id]


def _matches_request(
    existing: SourceRevisionIngested,
    requested: SourceDocument,
    chunking: ChunkingConfig,
) -> bool:
    source = existing.source
    return (
        source.source_id == requested.source_id
        and source.kind is requested.kind
        and source.title == requested.title
        and source.media_type == requested.media_type
        and source.checksum_sha256 == requested.checksum_sha256
        and source.byte_length == requested.byte_length
        and source.trust_level == requested.trust_level
        and source.source_role == requested.source_role
        and source.blob == requested.blob
        and source.normalized_blob == requested.normalized_blob
        and source.normalization_version == requested.normalization_version
        and source.normalized_character_length == requested.normalized_character_length
        and source.structure_origin is requested.structure_origin
        and source.ingestion_method == requested.ingestion_method
        and source.content_origin is requested.content_origin
        and source.conversion_provenance == requested.conversion_provenance
        and existing.chunking.version == chunking.version
        and existing.chunking.max_characters == chunking.max_characters
    )
