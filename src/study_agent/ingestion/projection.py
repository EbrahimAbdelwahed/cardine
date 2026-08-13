"""Projection encoding and reduction for immutable source revisions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import DomainEvent
from study_agent.domain.identifiers import BlobId, SubstrateId
from study_agent.domain.provenance import DocumentConversionProvenance
from study_agent.domain.source import BlobRef, SourceChunk, SourceDocument
from study_agent.state import EventRegistry

from .events import (
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    SOURCE_REVISION_SELECTED,
    SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
    BlobLoader,
    PersistedChunkingConfig,
    SourceRevisionIngested,
    SourceRevisionSelected,
    decode_source_revision_event,
    decode_source_revision_selected_event,
)
from .identity import CHUNK_MAX_CHARACTERS, CHUNKER_POLICY_VERSION
from .substrate_events import (
    SOURCE_SUBSTRATE_PRODUCED,
    SOURCE_SUBSTRATE_PRODUCED_SCHEMA_VERSION,
    decode_substrate_produced_event,
)
from .substrate_projection import reduce_substrate_produced


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _blob(blob: BlobRef) -> JsonObject:
    return {
        "id": str(blob.id),
        "checksum_sha256": blob.checksum_sha256,
        "byte_length": blob.byte_length,
    }


def _conversion(provenance: DocumentConversionProvenance) -> JsonObject:
    return {
        "pdf_sha256": provenance.pdf_sha256,
        "markdown_sha256": provenance.markdown_sha256,
        "adapter_identity": provenance.adapter_identity,
        "adapter_version": provenance.adapter_version,
        "manifest_fingerprint": provenance.manifest_fingerprint,
        "normalizer_policy": provenance.normalizer_policy,
        "limitations": provenance.limitations,
        "assets_omitted": provenance.assets_omitted,
        "page_count": provenance.page_count,
        "page_spans": tuple(
            {
                "page": span.page,
                "start_offset": span.start_offset,
                "end_offset": span.end_offset,
            }
            for span in provenance.page_spans
        ),
        "schema_version": provenance.schema_version,
    }


def source_manifest(source: SourceDocument) -> JsonObject:
    manifest: dict[str, JsonValue] = {
        "source_id": str(source.source_id),
        "revision_id": str(source.revision_id),
        "kind": source.kind.value,
        "title": source.title,
        "media_type": source.media_type,
        "checksum_sha256": source.checksum_sha256,
        "byte_length": source.byte_length,
        "created_at": _timestamp(source.created_at),
        "trust_level": source.trust_level,
        "source_role": source.source_role,
        "blob": _blob(source.blob),
        "normalized_blob": _blob(source.normalized_blob),
        "normalization_version": source.normalization_version,
        "normalized_character_length": source.normalized_character_length,
        "structure_origin": source.structure_origin.value,
        "ingestion_method": source.ingestion_method,
        "content_origin": source.content_origin.value,
    }
    if source.conversion_provenance is not None:
        manifest["conversion_provenance"] = _conversion(source.conversion_provenance)
    return manifest


def chunk_manifest(chunk: SourceChunk) -> JsonObject:
    return {
        "chunk_id": str(chunk.chunk_id),
        "source_id": str(chunk.source_id),
        "revision_id": str(chunk.revision_id),
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "section_path": chunk.section_path,
        "ordinal": chunk.ordinal,
        "checksum_sha256": chunk.checksum_sha256,
        "chunker_version": chunk.chunker_version,
        "metadata": chunk.metadata,
    }


def source_revision_payload(
    source: SourceDocument,
    chunks: tuple[SourceChunk, ...],
    *,
    chunker_version: str = CHUNKER_POLICY_VERSION,
    max_characters: int = CHUNK_MAX_CHARACTERS,
) -> JsonObject:
    chunking = PersistedChunkingConfig(chunker_version, max_characters)
    decoded = SourceRevisionIngested(source, chunks, source.normalized_character_length, chunking)
    return {
        "source": source_manifest(decoded.source),
        "chunks": tuple(chunk_manifest(chunk) for chunk in decoded.chunks),
        "normalized_character_length": decoded.normalized_character_length,
        "chunking": {
            "version": decoded.chunking.version,
            "max_characters": decoded.chunking.max_characters,
        },
    }


def _mapping(value: JsonValue | None, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"projection field {name} must be an object")
    return value


def _legacy_substrate_manifest(normalized_blob: BlobRef, character_length: int) -> JsonObject:
    """Return the bytes-only substrate view shared by v0.1 and v0.2."""
    return {
        "blob": _blob(normalized_blob),
        "character_length": character_length,
        "substrate_id": f"substrate:sha256:{normalized_blob.checksum_sha256}",
    }


def ensure_legacy_substrates(state: JsonObject) -> Mapping[str, JsonValue]:
    """Materialize legacy substrates in a persisted v0.1 projection.

    This migration is projection-only: the append-only event stream remains
    unchanged and the operation is deterministic from the existing source
    manifests.
    """
    sources = _mapping(state.get("sources", {}), "sources")
    substrates = dict(_mapping(state.get("substrates", {}), "substrates"))
    changed = False
    for source_id, source_value in sources.items():
        source = _mapping(source_value, f"sources.{source_id}")
        revisions = _mapping(source.get("revisions", {}), f"sources.{source_id}.revisions")
        for revision_id, revision_value in revisions.items():
            revision = _mapping(
                revision_value,
                f"sources.{source_id}.revisions.{revision_id}",
            )
            manifest = _mapping(
                revision.get("source"),
                f"sources.{source_id}.revisions.{revision_id}.source",
            )
            normalized = _mapping(
                manifest.get("normalized_blob"),
                "normalized_blob",
            )
            checksum = normalized.get("checksum_sha256")
            blob_id = normalized.get("id")
            byte_length = normalized.get("byte_length")
            character_length = revision.get("normalized_character_length")
            if (
                not isinstance(checksum, str)
                or not isinstance(blob_id, str)
                or blob_id != f"sha256:{checksum}"
                or type(byte_length) is not int
                or type(character_length) is not int
                or character_length < 1
            ):
                raise ValueError("legacy normalized blob manifest is invalid")
            substrate_ref = BlobRef(
                BlobId(blob_id),
                checksum,
                byte_length,
            )
            substrate_id = f"substrate:sha256:{checksum}"
            candidate = _legacy_substrate_manifest(substrate_ref, character_length)
            existing = substrates.get(substrate_id)
            if existing is not None and existing != candidate:
                raise ValueError("legacy substrate id already exists with different bytes")
            if existing is None:
                substrates[substrate_id] = candidate
                changed = True
    if not changed:
        return state
    return {**state, "substrates": substrates}


def reduce_source_revision(
    state: JsonObject, _: DomainEvent, payload: SourceRevisionIngested
) -> Mapping[str, JsonValue]:
    sources = dict(_mapping(state.get("sources", {}), "sources"))
    chunks = dict(_mapping(state.get("chunks", {}), "chunks"))
    source_id = str(payload.source.source_id)
    revision_id = str(payload.source.revision_id)
    existing_source = dict(_mapping(sources.get(source_id, {}), f"sources.{source_id}"))
    revisions = dict(
        _mapping(existing_source.get("revisions", {}), f"sources.{source_id}.revisions")
    )
    revision_ids_value = existing_source.get("revision_ids", ())
    if not isinstance(revision_ids_value, tuple) or any(
        not isinstance(item, str) for item in revision_ids_value
    ):
        raise ValueError("source revision_ids projection field is invalid")
    revision_ids = cast(tuple[str, ...], revision_ids_value)
    manifest: JsonObject = {
        "source": source_manifest(payload.source),
        "normalized_character_length": payload.normalized_character_length,
        "chunking": {
            "version": payload.chunking.version,
            "max_characters": payload.chunking.max_characters,
        },
    }
    if revision_id in revisions:
        if revisions[revision_id] != manifest:
            raise ValueError("revision id already exists with different immutable metadata")
        existing_chunk_ids = {
            chunk_id
            for chunk_id, value in chunks.items()
            if isinstance(value, Mapping)
            and value.get("source_id") == source_id
            and value.get("revision_id") == revision_id
        }
        incoming_chunk_ids = {str(chunk.chunk_id) for chunk in payload.chunks}
        if existing_chunk_ids != incoming_chunk_ids:
            raise ValueError("revision id already exists with a different immutable chunk set")
    else:
        revisions[revision_id] = manifest
        revision_ids = (*revision_ids, revision_id)

    for chunk in payload.chunks:
        chunk_id = str(chunk.chunk_id)
        encoded = chunk_manifest(chunk)
        if chunk_id in chunks and chunks[chunk_id] != encoded:
            raise ValueError("chunk id already exists with different immutable metadata")
        chunks[chunk_id] = encoded

    sources[source_id] = {
        "revision_ids": revision_ids,
        "revisions": revisions,
        "current_revision_id": revision_id,
    }
    # v0.1 events remain untouched.  Their normalized blob is already a
    # verified canonical UTF-8 artifact, so the v0.2 substrate view can expose
    # a deterministic legacy mapping without emitting a second event.
    normalized_blob = payload.source.normalized_blob
    legacy_substrate_id = SubstrateId(f"substrate:sha256:{normalized_blob.checksum_sha256}")
    substrates = dict(_mapping(state.get("substrates", {}), "substrates"))
    substrate_key = str(legacy_substrate_id)
    legacy_manifest = _legacy_substrate_manifest(
        normalized_blob, payload.source.normalized_character_length
    )
    existing_legacy = substrates.get(substrate_key)
    if existing_legacy is not None and existing_legacy != legacy_manifest:
        raise ValueError("legacy substrate id already exists with different metadata")
    substrates[substrate_key] = legacy_manifest
    return {**state, "sources": sources, "chunks": chunks, "substrates": substrates}


def reduce_source_revision_selected(
    state: JsonObject, _: DomainEvent, payload: SourceRevisionSelected
) -> Mapping[str, JsonValue]:
    sources = dict(_mapping(state.get("sources", {}), "sources"))
    source_id = str(payload.source_id)
    revision_id = str(payload.revision_id)
    existing_source = dict(_mapping(sources.get(source_id, {}), f"sources.{source_id}"))
    revisions = _mapping(existing_source.get("revisions", {}), f"sources.{source_id}.revisions")
    if revision_id not in revisions:
        raise ValueError("selected revision must already exist for its source")
    revision_ids = existing_source.get("revision_ids", ())
    if not isinstance(revision_ids, tuple) or any(
        not isinstance(item, str) for item in revision_ids
    ):
        raise ValueError("source revision_ids projection field is invalid")
    if revision_id not in revision_ids:
        raise ValueError("selected revision must belong to immutable revision history")
    existing_source["current_revision_id"] = revision_id
    sources[source_id] = existing_source
    return {**state, "sources": sources}


def register_source_revision_events(registry: EventRegistry, load_blob: BlobLoader) -> None:
    registry.register_projection_migration(ensure_legacy_substrates)
    registry.register_event(
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SCHEMA_VERSION,
        lambda event: decode_source_revision_event(event, load_blob),
        reduce_source_revision,
    )
    registry.register_event(
        SOURCE_REVISION_SELECTED,
        SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
        decode_source_revision_selected_event,
        reduce_source_revision_selected,
    )
    registry.register_event(
        SOURCE_SUBSTRATE_PRODUCED,
        SOURCE_SUBSTRATE_PRODUCED_SCHEMA_VERSION,
        lambda event: decode_substrate_produced_event(event, load_blob),
        reduce_substrate_produced,
    )
