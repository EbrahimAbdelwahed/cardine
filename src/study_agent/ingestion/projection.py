"""Projection encoding and reduction for immutable source revisions."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.artifact import ArtifactRevisionStatus, LessonMaterialVariant
from study_agent.domain.events import DomainEvent
from study_agent.domain.identifiers import BlobId, SubstrateId
from study_agent.domain.provenance import (
    ContentOrigin,
    DocumentConversionProvenance,
    GeneratedDocumentProvenance,
    generated_document_provenance_to_json,
)
from study_agent.domain.source import BlobRef, SourceChunk, SourceDocument, SourceKind
from study_agent.state import EventRegistry

from .events import (
    GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
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
from .identity import (
    CHUNK_MAX_CHARACTERS,
    CHUNKER_POLICY_VERSION,
    GENERATED_MARKDOWN_INGESTION_METHOD,
)
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


def _generated(provenance: GeneratedDocumentProvenance) -> JsonObject:
    return generated_document_provenance_to_json(provenance)


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
    if source.generated_provenance is not None:
        manifest["generated_provenance"] = _generated(source.generated_provenance)
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
    if source.content_origin is ContentOrigin.GENERATED or source.generated_provenance is not None:
        raise ValueError("v1 source payload cannot represent generated provenance")
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


def generated_source_revision_payload(
    source: SourceDocument,
    chunks: tuple[SourceChunk, ...],
    *,
    chunker_version: str = CHUNKER_POLICY_VERSION,
    max_characters: int = CHUNK_MAX_CHARACTERS,
) -> JsonObject:
    """Encode a generated source without embedding its Markdown bytes."""

    if source.content_origin is not ContentOrigin.GENERATED:
        raise ValueError("generated source payload requires generated provenance")
    if source.generated_provenance is None:
        raise ValueError("generated source payload requires generated provenance")
    if source.conversion_provenance is not None:
        raise ValueError("generated source payload cannot carry conversion provenance")
    if source.kind is not SourceKind.MARKDOWN:
        raise ValueError("generated source payload requires Markdown kind")
    if (
        source.media_type != "text/markdown"
        or source.ingestion_method != GENERATED_MARKDOWN_INGESTION_METHOD
    ):
        raise ValueError("generated source payload has an unsupported Markdown contract")
    if source.structure_origin.value != "human_approved":
        raise ValueError("generated source payload requires human-approved structure")
    if source.blob != source.normalized_blob:
        raise ValueError("generated source blob and normalized blob must be identical")
    chunking = PersistedChunkingConfig(chunker_version, max_characters)
    opaque_chunks = tuple(
        SourceChunk(
            chunk.chunk_id,
            chunk.source_id,
            chunk.revision_id,
            chunk.start_offset,
            chunk.end_offset,
            (),
            chunk.ordinal,
            chunk.checksum_sha256,
            chunk.chunker_version,
            {"block_kind": "opaque"},
        )
        for chunk in chunks
    )
    decoded = SourceRevisionIngested(
        source, opaque_chunks, source.normalized_character_length, chunking
    )
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


def validate_generated_source_admission(
    state: JsonObject, event: DomainEvent, payload: SourceRevisionIngested
) -> None:
    """Validate generated-source admission against the canonical projection."""
    from hashlib import sha256

    from study_agent.artifacts.content import LessonMaterialContent, StudyArtifactEnvelope
    from study_agent.artifacts.identity import (
        GeneratedArtifactProvenance,
        artifact_provenance_from_bytes,
    )
    from study_agent.domain.provenance import ContentOrigin

    if event.schema_version != GENERATED_SOURCE_REVISION_SCHEMA_VERSION:
        raise ValueError("generated admission validator requires source.revision_ingested@2")
    provenance = payload.source.generated_provenance
    if provenance is None or payload.source.content_origin is not ContentOrigin.GENERATED:
        raise ValueError("generated source admission requires generated provenance")
    raw_artifacts = _mapping(state.get("study_artifacts"), "study_artifacts")
    revisions = _mapping(raw_artifacts.get("revisions"), "artifact revisions")
    artifacts = _mapping(raw_artifacts.get("artifacts"), "artifacts")
    batches = _mapping(raw_artifacts.get("batches"), "artifact batches")
    decisions = raw_artifacts.get("decisions")
    commands = _mapping(raw_artifacts.get("commands"), "artifact commands")
    if not isinstance(decisions, tuple):
        raise ValueError("artifact decisions projection is corrupt")
    revision_id = str(provenance.artifact_revision_id)
    raw_revision = revisions.get(revision_id)
    if not isinstance(raw_revision, Mapping):
        raise ValueError("generated source artifact revision is not projected")
    if raw_revision.get("status") != ArtifactRevisionStatus.ACCEPTED.value:
        raise ValueError("generated source artifact revision is not accepted")
    artifact_id = raw_revision.get("artifact_id")
    if not isinstance(artifact_id, str):
        raise ValueError("generated source artifact identity is corrupt")
    artifact = _mapping(artifacts.get(artifact_id), "generated artifact")
    if artifact.get("current_revision_id") != revision_id:
        raise ValueError("generated source artifact revision is not the current head")
    content_text = raw_revision.get("content")
    provenance_text = raw_revision.get("provenance")
    if not isinstance(content_text, str) or not isinstance(provenance_text, str):
        raise ValueError("generated artifact revision payload is corrupt")
    try:
        envelope = StudyArtifactEnvelope.from_bytes(content_text.encode())
        artifact_provenance = artifact_provenance_from_bytes(provenance_text.encode())
    except (TypeError, ValueError) as error:
        raise ValueError("generated artifact revision payload is invalid") from error
    if not isinstance(envelope.content, LessonMaterialContent):
        raise ValueError("generated source requires a lesson material artifact")
    content = envelope.content
    if envelope.kind.value != "lesson_material" or content.variant is not provenance.variant:
        raise ValueError("generated source variant does not match its artifact")
    if not isinstance(artifact_provenance, GeneratedArtifactProvenance):
        raise ValueError("generated source requires generated artifact provenance")
    if artifact_provenance.run_id != provenance.material_run_id:
        raise ValueError("generated source run does not match artifact provenance")
    if sha256(provenance_text.encode()).hexdigest() != provenance.artifact_provenance_sha256:
        raise ValueError("generated source artifact provenance hash does not match")
    if content.markdown_blob.checksum_sha256 != payload.source.normalized_blob.checksum_sha256:
        raise ValueError("generated source blob does not match lesson material")
    if content.direct_parent_blob_sha256 != provenance.direct_parent_blob_sha256:
        raise ValueError("generated source direct parent does not match artifact")
    if content.markdown_blob.byte_length != payload.source.blob.byte_length:
        raise ValueError("generated source blob length does not match artifact")
    batch_id = raw_revision.get("batch_id")
    if not isinstance(batch_id, str):
        raise ValueError("generated source artifact batch identity is corrupt")
    raw_batch = batches.get(batch_id)
    if not isinstance(raw_batch, Mapping) or raw_batch.get("origin") != "generated":
        raise ValueError("generated source artifact batch is not generated")
    if raw_batch.get("run_id") != str(provenance.material_run_id):
        raise ValueError("generated source batch run does not match")
    causation = str(provenance.human_decision_event_id)
    command = commands.get(causation)
    if not isinstance(command, Mapping) or command.get("result_id") != revision_id:
        raise ValueError("generated source causation does not bind its artifact decision")
    matching_decisions = [
        item
        for item in decisions
        if isinstance(item, Mapping) and item.get("revision_id") == revision_id
    ]
    if len(matching_decisions) != 1:
        raise ValueError("generated source requires exactly one artifact decision")
    decision = matching_decisions[0]
    if (
        decision.get("decision") != "accept"
        or decision.get("policy_receipt") is not None
        or decision.get("decided_at")
        != provenance.human_decision_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    ):
        raise ValueError("generated source requires the exact HUMAN acceptance decision")
    revision_ids = raw_batch.get("revision_ids")
    if not isinstance(revision_ids, tuple) or len(revision_ids) != 2:
        raise ValueError("generated material batch must contain complete and study pair")
    pair: list[
        tuple[Mapping[str, JsonValue], LessonMaterialContent, GeneratedArtifactProvenance]
    ] = []
    for pair_id in revision_ids:
        if not isinstance(pair_id, str):
            raise ValueError("generated material pair revision identity is corrupt")
        pair_raw = revisions.get(pair_id)
        if not isinstance(pair_raw, Mapping):
            raise ValueError("generated material pair revision is missing")
        pair_content_text = pair_raw.get("content")
        pair_provenance_text = pair_raw.get("provenance")
        if not isinstance(pair_content_text, str) or not isinstance(pair_provenance_text, str):
            raise ValueError("generated material pair revision is corrupt")
        pair_envelope = StudyArtifactEnvelope.from_bytes(pair_content_text.encode())
        pair_provenance = artifact_provenance_from_bytes(pair_provenance_text.encode())
        if not isinstance(pair_envelope.content, LessonMaterialContent) or not isinstance(
            pair_provenance, GeneratedArtifactProvenance
        ):
            raise ValueError("generated material batch contains a non-material pair")
        pair.append((pair_raw, pair_envelope.content, pair_provenance))
    variants = {item[1].variant for item in pair}
    if variants != {LessonMaterialVariant.COMPLETE, LessonMaterialVariant.STUDY}:
        raise ValueError("generated material batch must contain complete and study variants")
    roots = {
        (str(item.source_id), str(item.revision_id))
        for item in artifact_provenance.source_commitments
    }
    if len(roots) != 1 or roots != {
        (str(provenance.root_source_id), str(provenance.root_revision_id))
    }:
        raise ValueError("generated material root lineage is not exact")
    for _, _, pair_provenance in pair:
        pair_roots = {
            (str(item.source_id), str(item.revision_id))
            for item in pair_provenance.source_commitments
        }
        if pair_provenance.run_id != provenance.material_run_id or pair_roots != roots:
            raise ValueError("generated material pair lineage is inconsistent")
    sources = _mapping(state.get("sources"), "sources")
    root_projection = _mapping(sources.get(str(provenance.root_source_id)), "root source")
    root_revisions = _mapping(root_projection.get("revisions"), "root revisions")
    root_revision = root_revisions.get(str(provenance.root_revision_id))
    if root_projection.get("current_revision_id") != str(provenance.root_revision_id):
        raise ValueError("generated material root revision is not current")
    root_manifest = _mapping(root_revision, "root revision")
    root_source = _mapping(root_manifest.get("source"), "root source manifest")
    root_normalized = _mapping(root_source.get("normalized_blob"), "root normalized blob")
    if (
        root_source.get("content_origin") == ContentOrigin.GENERATED.value
        or root_source.get("generated_provenance") is not None
    ):
        raise ValueError("generated material root must be an original admitted source")
    if (
        root_normalized.get("checksum_sha256") != provenance.root_normalized_blob_sha256
        or root_source.get("trust_level") != payload.source.trust_level
        or root_source.get("source_role") != payload.source.source_role
    ):
        raise ValueError("generated source trust, role, or root digest does not match")
    complete = next(item[1] for item in pair if item[1].variant is LessonMaterialVariant.COMPLETE)
    study = next(item[1] for item in pair if item[1].variant is LessonMaterialVariant.STUDY)
    if complete.direct_parent_blob_sha256 != provenance.root_normalized_blob_sha256:
        raise ValueError("complete material must parent the root normalized blob")
    if study.direct_parent_blob_sha256 != complete.markdown_blob.checksum_sha256:
        raise ValueError("study material must parent the complete material blob")
    if provenance.variant is LessonMaterialVariant.STUDY:
        complete_revision_id = next(
            pair_id
            for pair_id, (_, pair_content, _) in zip(revision_ids, pair, strict=True)
            if pair_content.variant is LessonMaterialVariant.COMPLETE
        )
        if not isinstance(complete_revision_id, str):
            raise ValueError("complete material revision identity is corrupt")
        complete_raw = revisions.get(complete_revision_id)
        complete_artifact_id = _mapping(complete_raw, "complete revision").get("artifact_id")
        if not isinstance(complete_artifact_id, str):
            raise ValueError("complete material artifact identity is corrupt")
        complete_artifact = _mapping(artifacts.get(complete_artifact_id), "complete artifact")
        if complete_artifact.get("current_revision_id") != complete_revision_id or _mapping(
            complete_raw, "complete revision"
        ).get("status") != ArtifactRevisionStatus.ACCEPTED.value:
            raise ValueError("study material requires an accepted current complete sibling")
        found_projected_complete = False
        for source_value in sources.values():
            source_item = _mapping(source_value, "generated source")
            for revision_value in _mapping(
                source_item.get("revisions"), "generated revisions"
            ).values():
                revision_item = _mapping(revision_value, "generated source revision")
                manifest = _mapping(revision_item.get("source"), "generated source manifest")
                generated = manifest.get("generated_provenance")
                if (
                    isinstance(generated, Mapping)
                    and generated.get("artifact_revision_id") == str(complete_revision_id)
                    and generated.get("variant") == LessonMaterialVariant.COMPLETE.value
                    and generated.get("root_source_id") == str(provenance.root_source_id)
                    and generated.get("root_revision_id") == str(provenance.root_revision_id)
                    and generated.get("material_run_id") == str(provenance.material_run_id)
                ):
                    found_projected_complete = True
        if not found_projected_complete:
            raise ValueError("study material requires a prior projected complete source")


def reduce_generated_source_revision(
    state: JsonObject, event: DomainEvent, payload: SourceRevisionIngested
) -> Mapping[str, JsonValue]:
    validate_generated_source_admission(state, event, payload)
    return reduce_source_revision(state, event, payload)


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
        SOURCE_REVISION_INGESTED,
        GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
        lambda event: decode_source_revision_event(event, load_blob),
        reduce_generated_source_revision,
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
