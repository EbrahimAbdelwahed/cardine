"""Typed source-revision event payloads and strict canonical decoding."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import DomainEvent
from study_agent.domain.identifiers import BlobId, ChunkId, RevisionId, SourceId
from study_agent.domain.provenance import (
    ContentOrigin,
    DocumentConversionProvenance,
    DocumentPageSpan,
    StructureOrigin,
)
from study_agent.domain.source import BlobRef, SourceChunk, SourceDocument, SourceKind

from .chunking import CHUNKER_VERSION, ChunkingConfig, chunk_text
from .identity import (
    NORMALIZATION_POLICY_VERSION,
    chunk_id_for,
    legacy_revision_id_for,
    revision_id_for,
    source_event_id_for,
    source_kind_contract,
    source_revision_selected_event_id_for,
)
from .normalization import normalize_utf8

SOURCE_REVISION_INGESTED = "source.revision_ingested"
SOURCE_REVISION_SCHEMA_VERSION = 1
SOURCE_REVISION_SELECTED = "source.revision_selected"
SOURCE_REVISION_SELECTED_SCHEMA_VERSION = 1

_BLOB_KEYS = frozenset({"id", "checksum_sha256", "byte_length"})
_SOURCE_KEYS = frozenset(
    {
        "source_id",
        "revision_id",
        "kind",
        "title",
        "media_type",
        "checksum_sha256",
        "byte_length",
        "created_at",
        "trust_level",
        "source_role",
        "blob",
        "normalized_blob",
        "normalization_version",
        "normalized_character_length",
        "structure_origin",
        "ingestion_method",
        "content_origin",
    }
)
_CONVERSION_KEYS = frozenset(
    {
        "pdf_sha256",
        "markdown_sha256",
        "adapter_identity",
        "adapter_version",
        "manifest_fingerprint",
        "normalizer_policy",
        "limitations",
        "assets_omitted",
        "page_count",
        "page_spans",
        "schema_version",
    }
)
_LEGACY_CONVERSION_KEYS = _CONVERSION_KEYS - {"page_spans"}
_CHUNK_KEYS = frozenset(
    {
        "chunk_id",
        "source_id",
        "revision_id",
        "start_offset",
        "end_offset",
        "section_path",
        "ordinal",
        "checksum_sha256",
        "chunker_version",
        "metadata",
    }
)
_CHUNKING_KEYS = frozenset({"version", "max_characters"})

type BlobLoader = Callable[[BlobRef], bytes]


@dataclass(frozen=True, slots=True)
class PersistedChunkingConfig:
    version: str
    max_characters: int

    def __post_init__(self) -> None:
        if not self.version or self.version != self.version.strip():
            raise ValueError("chunking.version must be non-empty trimmed text")
        if self.max_characters < 1:
            raise ValueError("chunking.max_characters must be positive")


@dataclass(frozen=True, slots=True)
class SourceRevisionIngested:
    source: SourceDocument
    chunks: tuple[SourceChunk, ...]
    normalized_character_length: int
    chunking: PersistedChunkingConfig

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunks", tuple(self.chunks))
        if self.normalized_character_length < 1:
            raise ValueError("normalized_character_length must be positive")
        if self.normalized_character_length != self.source.normalized_character_length:
            raise ValueError("normalized_character_length must match source manifest")
        _validate_chunks(
            self.source,
            self.chunks,
            self.normalized_character_length,
            self.chunking,
        )


@dataclass(frozen=True, slots=True)
class SourceRevisionSelected:
    source_id: SourceId
    revision_id: RevisionId


def source_revision_selected_payload(source_id: SourceId, revision_id: RevisionId) -> JsonObject:
    return {"source_id": str(source_id), "revision_id": str(revision_id)}


def decode_source_revision_selected(payload: JsonObject) -> SourceRevisionSelected:
    decoded = _object(
        payload,
        "payload",
        frozenset({"source_id", "revision_id"}),
    )
    return SourceRevisionSelected(
        SourceId(_text(decoded.get("source_id"), "source_id")),
        RevisionId(_text(decoded.get("revision_id"), "revision_id")),
    )


def decode_source_revision_selected_event(event: DomainEvent) -> SourceRevisionSelected:
    if (
        event.event_type != SOURCE_REVISION_SELECTED
        or event.schema_version != SOURCE_REVISION_SELECTED_SCHEMA_VERSION
    ):
        raise ValueError("event envelope does not match source.revision_selected@1")
    decoded = decode_source_revision_selected(event.payload)
    expected_id = source_revision_selected_event_id_for(
        event.course_id,
        decoded.source_id,
        decoded.revision_id,
        event.course_sequence,
    )
    if event.event_id != expected_id:
        raise ValueError("event_id does not match revision selection identity")
    return decoded


def _object(value: JsonValue | None, name: str, keys: frozenset[str]) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    actual = frozenset(value)
    if actual != keys:
        missing = sorted(keys - actual)
        extra = sorted(actual - keys)
        raise ValueError(f"{name} fields mismatch; missing={missing}, extra={extra}")
    return value


def _text(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _integer(value: JsonValue | None, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _blob(value: JsonValue | None, name: str) -> BlobRef:
    payload = _object(value, name, _BLOB_KEYS)
    checksum = _text(payload.get("checksum_sha256"), f"{name}.checksum_sha256")
    blob_id = _text(payload.get("id"), f"{name}.id")
    if blob_id != f"sha256:{checksum}":
        raise ValueError(f"{name}.id must match its SHA-256 checksum")
    return BlobRef(
        BlobId(blob_id),
        checksum,
        _integer(payload.get("byte_length"), f"{name}.byte_length"),
    )


def _timestamp(value: JsonValue | None) -> datetime:
    text = _text(value, "source.created_at")
    try:
        result = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError("source.created_at must be an ISO-8601 timestamp") from error
    if result.tzinfo is None or result.utcoffset() is None:
        raise ValueError("source.created_at must be timezone-aware")
    return result


def _conversion(value: JsonValue | None) -> DocumentConversionProvenance:
    if not isinstance(value, Mapping) or frozenset(value) not in {
        _CONVERSION_KEYS,
        _LEGACY_CONVERSION_KEYS,
    }:
        raise ValueError("source.conversion_provenance fields mismatch")
    payload = value
    limitations = payload.get("limitations")
    if not isinstance(limitations, tuple) or any(not isinstance(item, str) for item in limitations):
        raise ValueError("conversion limitations must be an array of strings")
    page_count = payload.get("page_count")
    if page_count is not None and type(page_count) is not int:
        raise ValueError("conversion page_count must be an integer or null")
    raw_spans = payload.get("page_spans", ())
    if not isinstance(raw_spans, tuple):
        raise ValueError("conversion page_spans must be an array")
    spans: list[DocumentPageSpan] = []
    for index, value in enumerate(raw_spans):
        span = _object(
            value,
            f"conversion.page_spans[{index}]",
            frozenset({"page", "start_offset", "end_offset"}),
        )
        spans.append(
            DocumentPageSpan(
                page=_integer(span.get("page"), "page"),
                start_offset=_integer(span.get("start_offset"), "start_offset"),
                end_offset=_integer(span.get("end_offset"), "end_offset"),
            )
        )
    return DocumentConversionProvenance(
        pdf_sha256=_text(payload.get("pdf_sha256"), "conversion.pdf_sha256"),
        markdown_sha256=_text(payload.get("markdown_sha256"), "conversion.markdown_sha256"),
        adapter_identity=_text(payload.get("adapter_identity"), "conversion.adapter_identity"),
        adapter_version=_text(payload.get("adapter_version"), "conversion.adapter_version"),
        manifest_fingerprint=_text(
            payload.get("manifest_fingerprint"), "conversion.manifest_fingerprint"
        ),
        normalizer_policy=_text(payload.get("normalizer_policy"), "conversion.normalizer_policy"),
        limitations=cast(tuple[str, ...], limitations),
        assets_omitted=payload.get("assets_omitted") is True,
        page_count=page_count,
        page_spans=tuple(spans),
        schema_version=_integer(payload.get("schema_version"), "conversion.schema_version"),
    )


def _source(value: JsonValue | None) -> SourceDocument:
    if not isinstance(value, Mapping):
        raise ValueError("source must be an object")
    actual = frozenset(value)
    if actual not in {_SOURCE_KEYS, _SOURCE_KEYS | {"conversion_provenance"}}:
        raise ValueError("source fields mismatch")
    payload = value
    try:
        kind = SourceKind(_text(payload.get("kind"), "source.kind"))
        structure_origin = StructureOrigin(
            _text(payload.get("structure_origin"), "source.structure_origin")
        )
        content_origin = ContentOrigin(
            _text(payload.get("content_origin"), "source.content_origin")
        )
    except ValueError as error:
        raise ValueError("source contains an unsupported enum value") from error
    return SourceDocument(
        source_id=SourceId(_text(payload.get("source_id"), "source.source_id")),
        revision_id=RevisionId(_text(payload.get("revision_id"), "source.revision_id")),
        kind=kind,
        title=_text(payload.get("title"), "source.title"),
        media_type=_text(payload.get("media_type"), "source.media_type"),
        checksum_sha256=_text(payload.get("checksum_sha256"), "source.checksum_sha256"),
        byte_length=_integer(payload.get("byte_length"), "source.byte_length"),
        created_at=_timestamp(payload.get("created_at")),
        trust_level=_integer(payload.get("trust_level"), "source.trust_level"),
        source_role=_text(payload.get("source_role"), "source.source_role"),
        blob=_blob(payload.get("blob"), "source.blob"),
        normalized_blob=_blob(payload.get("normalized_blob"), "source.normalized_blob"),
        normalization_version=_text(
            payload.get("normalization_version"), "source.normalization_version"
        ),
        normalized_character_length=_integer(
            payload.get("normalized_character_length"),
            "source.normalized_character_length",
        ),
        structure_origin=structure_origin,
        ingestion_method=_text(payload.get("ingestion_method"), "source.ingestion_method"),
        content_origin=content_origin,
        conversion_provenance=(
            _conversion(payload.get("conversion_provenance"))
            if "conversion_provenance" in payload
            else None
        ),
    )


def _chunk(value: JsonValue, index: int) -> SourceChunk:
    name = f"chunks[{index}]"
    payload = _object(value, name, _CHUNK_KEYS)
    section_path = payload.get("section_path")
    if not isinstance(section_path, tuple) or any(
        not isinstance(section, str) for section in section_path
    ):
        raise ValueError(f"{name}.section_path must be an array of strings")
    metadata = payload.get("metadata")
    if not isinstance(metadata, Mapping):
        raise ValueError(f"{name}.metadata must be an object")
    return SourceChunk(
        chunk_id=ChunkId(_text(payload.get("chunk_id"), f"{name}.chunk_id")),
        source_id=SourceId(_text(payload.get("source_id"), f"{name}.source_id")),
        revision_id=RevisionId(_text(payload.get("revision_id"), f"{name}.revision_id")),
        start_offset=_integer(payload.get("start_offset"), f"{name}.start_offset"),
        end_offset=_integer(payload.get("end_offset"), f"{name}.end_offset"),
        section_path=cast(tuple[str, ...], section_path),
        ordinal=_integer(payload.get("ordinal"), f"{name}.ordinal"),
        checksum_sha256=_text(payload.get("checksum_sha256"), f"{name}.checksum_sha256"),
        chunker_version=_text(payload.get("chunker_version"), f"{name}.chunker_version"),
        metadata=metadata,
    )


def _chunking(value: JsonValue | None) -> PersistedChunkingConfig:
    payload = _object(value, "chunking", _CHUNKING_KEYS)
    return PersistedChunkingConfig(
        _text(payload.get("version"), "chunking.version"),
        _integer(payload.get("max_characters"), "chunking.max_characters"),
    )


def _validate_chunks(
    source: SourceDocument,
    chunks: tuple[SourceChunk, ...],
    normalized_character_length: int,
    chunking: PersistedChunkingConfig,
) -> None:
    if not chunks:
        raise ValueError("chunks must contain at least one chunk")
    chunk_ids: set[ChunkId] = set()
    previous_end = 0
    for expected_ordinal, chunk in enumerate(chunks):
        if chunk.source_id != source.source_id or chunk.revision_id != source.revision_id:
            raise ValueError("every chunk must belong to the decoded source revision")
        if chunk.chunk_id in chunk_ids:
            raise ValueError("chunk ids must be unique within a revision")
        if chunk.ordinal != expected_ordinal:
            raise ValueError("chunk ordinals must be contiguous and start at zero")
        if chunk.start_offset < previous_end:
            raise ValueError("chunk spans must be ordered and non-overlapping")
        if chunk.end_offset > normalized_character_length:
            raise ValueError("chunk span exceeds normalized character length")
        if chunk.chunker_version != chunking.version:
            raise ValueError("chunk version must match persisted chunking config")
        chunk_ids.add(chunk.chunk_id)
        previous_end = chunk.end_offset


def decode_source_revision_ingested(payload: JsonObject) -> SourceRevisionIngested:
    if frozenset(payload) != {
        "source",
        "chunks",
        "normalized_character_length",
        "chunking",
    }:
        raise ValueError(
            "payload must contain exactly source, chunks, normalized_character_length, and chunking"
        )
    source = _source(payload.get("source"))
    chunks_value = payload.get("chunks")
    if not isinstance(chunks_value, tuple):
        raise ValueError("chunks must be an array")
    chunks = tuple(_chunk(value, index) for index, value in enumerate(chunks_value))
    return SourceRevisionIngested(
        source,
        chunks,
        _integer(payload.get("normalized_character_length"), "normalized_character_length"),
        _chunking(payload.get("chunking")),
    )


def _verified_blob(load_blob: BlobLoader, ref: BlobRef, name: str) -> bytes:
    content = load_blob(ref)
    if not isinstance(content, bytes):
        raise ValueError(f"{name} loader must return bytes")
    if len(content) != ref.byte_length:
        raise ValueError(f"{name} byte length does not match loaded content")
    if sha256(content).hexdigest() != ref.checksum_sha256:
        raise ValueError(f"{name} checksum does not match loaded content")
    return content


def decode_source_revision_event(
    event: DomainEvent, load_blob: BlobLoader
) -> SourceRevisionIngested:
    if (
        event.event_type != SOURCE_REVISION_INGESTED
        or event.schema_version != SOURCE_REVISION_SCHEMA_VERSION
    ):
        raise ValueError("event envelope does not match source.revision_ingested@1")
    decoded = decode_source_revision_ingested(event.payload)
    source = decoded.source
    original = _verified_blob(load_blob, source.blob, "source.blob")
    normalized_bytes = _verified_blob(load_blob, source.normalized_blob, "source.normalized_blob")
    try:
        normalized_text = normalized_bytes.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise ValueError("normalized blob must contain strict UTF-8") from error
    if normalize_utf8(normalized_bytes).content != normalized_bytes:
        raise ValueError("normalized blob is not canonical newline-normalized NFC text")
    if source.content_origin is ContentOrigin.ORIGINAL:
        try:
            expected_normalized = normalize_utf8(original).content
        except ValueError as error:
            raise ValueError("original blob must contain strict UTF-8") from error
        if normalized_bytes != expected_normalized:
            raise ValueError("normalized blob does not match canonical normalization of original")
    elif source.content_origin is ContentOrigin.EXTRACTED:
        provenance = source.conversion_provenance
        if provenance is None:
            raise ValueError("extracted source lacks conversion provenance")
        if provenance.pdf_sha256 != sha256(original).hexdigest():
            raise ValueError("PDF provenance does not match original blob")
        if provenance.markdown_sha256 != sha256(normalized_bytes).hexdigest():
            raise ValueError("Markdown provenance does not match normalized blob")
    else:
        raise ValueError("ingested source content origin is unsupported")
    if decoded.normalized_character_length != len(normalized_text):
        raise ValueError("normalized_character_length does not match normalized text")
    if source.normalization_version != NORMALIZATION_POLICY_VERSION:
        raise ValueError("unsupported normalization version")
    if decoded.chunking.version != CHUNKER_VERSION:
        raise ValueError("unsupported chunking version")
    expected_media_type, expected_method = source_kind_contract(source.kind)
    if source.media_type != expected_media_type or source.ingestion_method != expected_method:
        raise ValueError("source kind, media type, and ingestion method are inconsistent")
    if source.structure_origin is not StructureOrigin.MECHANICALLY_EXTRACTED:
        raise ValueError("ingested source structure_origin must be mechanically_extracted")
    if source.created_at != event.occurred_at:
        raise ValueError("source.created_at must equal event.occurred_at")
    expected_revision = revision_id_for(
        original_sha256=sha256(original).hexdigest(),
        source_id=source.source_id,
        kind=source.kind,
        title=source.title,
        trust_level=source.trust_level,
        source_role=source.source_role,
        normalization_version=source.normalization_version,
        chunker_version=decoded.chunking.version,
        max_characters=decoded.chunking.max_characters,
    )
    legacy_revision = legacy_revision_id_for(
        original_sha256=sha256(original).hexdigest(),
        source_id=source.source_id,
        kind=source.kind,
        normalization_version=source.normalization_version,
        chunker_version=decoded.chunking.version,
        max_characters=decoded.chunking.max_characters,
    )
    if source.revision_id not in (expected_revision, legacy_revision):
        raise ValueError("revision_id does not match canonical immutable inputs")
    if event.event_id != source_event_id_for(event.course_id, source.revision_id):
        raise ValueError("event_id does not match course and revision identity")
    for chunk in decoded.chunks:
        span = normalized_text[chunk.start_offset : chunk.end_offset]
        digest = sha256(span.encode("utf-8")).hexdigest()
        if chunk.checksum_sha256 != digest:
            raise ValueError("chunk checksum does not match normalized text span")
        expected_chunk = chunk_id_for(
            source_id=chunk.source_id,
            revision_id=chunk.revision_id,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            checksum_sha256=chunk.checksum_sha256,
            chunker_version=chunk.chunker_version,
        )
        if chunk.chunk_id != expected_chunk:
            raise ValueError("chunk_id does not match canonical span identity")
    reconstructed = chunk_text(
        normalized_text,
        source_id=source.source_id,
        revision_id=source.revision_id,
        kind=source.kind,
        config=ChunkingConfig(
            max_characters=decoded.chunking.max_characters,
            version=decoded.chunking.version,
        ),
    )
    if decoded.chunks != reconstructed:
        raise ValueError("supplied chunks do not exactly match canonical chunking output")
    return decoded
