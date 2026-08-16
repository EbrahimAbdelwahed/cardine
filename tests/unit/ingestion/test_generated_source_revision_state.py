from __future__ import annotations

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest

from study_agent.domain import (
    Actor,
    ArtifactRevisionId,
    BlobRef,
    ContentOrigin,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    GeneratedDocumentProvenance,
    LessonMaterialVariant,
    PrincipalKind,
    RevisionId,
    RunId,
    SessionId,
    SourceId,
    StructureOrigin,
    generated_document_provenance_from_bytes,
    generated_document_provenance_from_json,
    generated_document_provenance_to_bytes,
    generated_document_provenance_to_json,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.source import SourceDocument, SourceKind
from study_agent.ingestion import (
    CHUNKER_VERSION,
    GENERATED_MARKDOWN_INGESTION_METHOD,
    GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
    NORMALIZATION_POLICY_VERSION,
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    ChunkingConfig,
    chunk_text,
    decode_source_revision_event,
    generated_revision_id_for,
    generated_source_event_id_for,
    generated_source_id_for,
    generated_source_revision_payload,
    reduce_generated_source_revision,
    revision_id_for,
    source_event_id_for,
    source_revision_payload,
)
from study_agent.ingestion.preparation import predicted_blob, prepare_text
from study_agent.state.serialization import event_from_bytes, event_to_bytes

NOW = datetime(2026, 8, 14, 16, 0, tzinfo=UTC)
COURSE = CourseId("course-materials")
CORRELATION = CorrelationId("correlation-materials")
ROOT_SOURCE = SourceId("root-sbobina")
ROOT_ROLE = "primary"


def _v1_event() -> tuple[DomainEvent, dict[BlobRef, bytes]]:
    original = "# Root\r\n\r\nCafe\u0301 valve.".encode()
    prepared = prepare_text(original)
    revision = revision_id_for(
        original_sha256=sha256(original).hexdigest(),
        source_id=ROOT_SOURCE,
        kind=SourceKind.MARKDOWN,
        title="Root notes",
        trust_level=90,
        source_role=ROOT_ROLE,
        normalization_version=NORMALIZATION_POLICY_VERSION,
        chunker_version=CHUNKER_VERSION,
        max_characters=1200,
    )
    source = SourceDocument(
        source_id=ROOT_SOURCE,
        revision_id=revision,
        kind=SourceKind.MARKDOWN,
        title="Root notes",
        media_type="text/markdown",
        checksum_sha256=prepared.original_blob.checksum_sha256,
        byte_length=prepared.original_blob.byte_length,
        created_at=NOW,
        trust_level=90,
        source_role=ROOT_ROLE,
        blob=prepared.original_blob,
        normalized_blob=prepared.normalized_blob,
        normalization_version=NORMALIZATION_POLICY_VERSION,
        normalized_character_length=len(prepared.normalized.text),
        structure_origin=StructureOrigin.MECHANICALLY_EXTRACTED,
        ingestion_method="utf8-markdown-v1",
        content_origin=ContentOrigin.ORIGINAL,
    )
    chunks = chunk_text(
        prepared.normalized.text,
        source_id=source.source_id,
        revision_id=source.revision_id,
        kind=source.kind,
        config=ChunkingConfig(1200, CHUNKER_VERSION),
    )
    payload = source_revision_payload(source, chunks)
    event = DomainEvent(
        source_event_id_for(COURSE, revision),
        COURSE,
        1,
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SCHEMA_VERSION,
        Actor(PrincipalKind.SERVICE, "ingestion"),
        NOW,
        CORRELATION,
        payload,
    )
    return event, {
        prepared.original_blob: original,
        prepared.normalized_blob: prepared.normalized.content,
    }


def _v2_event() -> tuple[DomainEvent, dict[BlobRef, bytes]]:
    markdown = b"# Secret heading marker\n\nThree aortic cusps.\n"
    blob = predicted_blob(markdown)
    artifact_revision = ArtifactRevisionId("artifact-revision-complete")
    run = RunId("material-run-1")
    source_id = generated_source_id_for(
        course_id=COURSE,
        root_source_id=ROOT_SOURCE,
        root_revision_id=RevisionId("root-revision-1"),
        artifact_revision_id=artifact_revision,
        material_run_id=run,
        variant=LessonMaterialVariant.COMPLETE,
    )
    provenance = GeneratedDocumentProvenance(
        root_source_id=ROOT_SOURCE,
        root_revision_id=RevisionId("root-revision-1"),
        root_normalized_blob_sha256="a" * 64,
        artifact_revision_id=artifact_revision,
        artifact_provenance_sha256="b" * 64,
        material_run_id=run,
        variant=LessonMaterialVariant.COMPLETE,
        direct_parent_blob_sha256="a" * 64,
        human_decision_event_id=EventId("event-human-accept"),
        human_decision_at=NOW,
    )
    revision = generated_revision_id_for(
        source_id=source_id,
        root_source_id=ROOT_SOURCE,
        root_revision_id=RevisionId("root-revision-1"),
        artifact_revision_id=artifact_revision,
        material_run_id=run,
        variant=LessonMaterialVariant.COMPLETE,
        markdown_sha256=blob.checksum_sha256,
        title="Complete material",
        normalization_version=NORMALIZATION_POLICY_VERSION,
        chunker_version=CHUNKER_VERSION,
        max_characters=1200,
        trust_level=90,
        source_role=ROOT_ROLE,
        root_normalized_blob_sha256=provenance.root_normalized_blob_sha256,
        artifact_provenance_sha256=provenance.artifact_provenance_sha256,
        direct_parent_blob_sha256=provenance.direct_parent_blob_sha256,
        human_decision_event_id=provenance.human_decision_event_id,
        human_decision_at=provenance.human_decision_at,
    )
    source = SourceDocument(
        source_id=source_id,
        revision_id=revision,
        kind=SourceKind.MARKDOWN,
        title="Complete material",
        media_type="text/markdown",
        checksum_sha256=blob.checksum_sha256,
        byte_length=blob.byte_length,
        created_at=NOW,
        trust_level=90,
        source_role=ROOT_ROLE,
        blob=blob,
        normalized_blob=blob,
        normalization_version=NORMALIZATION_POLICY_VERSION,
        normalized_character_length=len(markdown.decode()),
        structure_origin=StructureOrigin.HUMAN_APPROVED,
        ingestion_method=GENERATED_MARKDOWN_INGESTION_METHOD,
        content_origin=ContentOrigin.GENERATED,
        generated_provenance=provenance,
    )
    chunks = chunk_text(
        markdown.decode(),
        source_id=source_id,
        revision_id=revision,
        kind=SourceKind.MARKDOWN,
        config=ChunkingConfig(1200, CHUNKER_VERSION),
    )
    payload = generated_source_revision_payload(source, chunks)
    event = DomainEvent(
        generated_source_event_id_for(COURSE, source_id, revision),
        COURSE,
        2,
        SOURCE_REVISION_INGESTED,
        GENERATED_SOURCE_REVISION_SCHEMA_VERSION,
        Actor(PrincipalKind.SERVICE, "generated-source-materializer"),
        NOW,
        CORRELATION,
        payload,
        causation_id=provenance.human_decision_event_id,
    )
    return event, {blob: markdown}


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def test_existing_v1_source_event_serialization_and_replay_remain_byte_stable() -> None:
    event, blobs = _v1_event()
    replayed = event_from_bytes(event_to_bytes(event))

    assert event_to_bytes(replayed) == event_to_bytes(event)
    decoded = decode_source_revision_event(replayed, blobs.__getitem__)
    assert decoded.source.content_origin is ContentOrigin.ORIGINAL
    assert decoded.source.generated_provenance is None


def test_generated_v2_round_trip_preserves_lineage_and_never_embeds_markdown() -> None:
    event, blobs = _v2_event()
    decoded = decode_source_revision_event(event, blobs.__getitem__)

    assert decoded.source.content_origin is ContentOrigin.GENERATED
    assert decoded.source.structure_origin is StructureOrigin.HUMAN_APPROVED
    assert decoded.source.generated_provenance is not None
    assert decoded.source.generated_provenance.variant is LessonMaterialVariant.COMPLETE
    assert b"Secret heading marker" not in event_to_bytes(event)
    assert b"Three aortic cusps" not in event_to_bytes(event)


def test_generated_v2_cannot_admit_self_asserted_approval_without_canonical_state() -> None:
    event, blobs = _v2_event()
    decoded = decode_source_revision_event(event, blobs.__getitem__)

    with pytest.raises(ValueError, match="artifact"):
        reduce_generated_source_revision({}, event, decoded)


@pytest.mark.parametrize("field,value", (("trust_level", 100), ("source_role", "exam")))
def test_generated_v2_identity_binds_inherited_retrieval_metadata(
    field: str, value: object
) -> None:
    event, blobs = _v2_event()
    payload = cast(dict[str, object], _plain(event.payload))
    source = cast(dict[str, object], payload["source"])
    source[field] = value

    with pytest.raises(ValueError, match="revision_id"):
        decode_source_revision_event(
            replace(event, payload=cast(JsonObject, payload)), blobs.__getitem__
        )


def test_generated_document_provenance_codec_is_exact_and_closed() -> None:
    event, blobs = _v2_event()
    decoded = decode_source_revision_event(event, blobs.__getitem__)
    provenance = decoded.source.generated_provenance
    assert provenance is not None

    encoded = generated_document_provenance_to_bytes(provenance)
    assert generated_document_provenance_from_bytes(encoded) == provenance
    decoded_json = generated_document_provenance_from_json(
        generated_document_provenance_to_json(provenance)
    )
    assert decoded_json == provenance
    assert b"Three aortic cusps" not in encoded

    forged = {**generated_document_provenance_to_json(provenance), "unknown": True}
    with pytest.raises(ValueError):
        generated_document_provenance_from_json(cast(JsonObject, forged))


@pytest.mark.parametrize(
    "mutation",
    ("wrong_origin", "wrong_structure", "missing_provenance", "inline_markdown"),
)
def test_generated_v2_rejects_forged_source_contracts(mutation: str) -> None:
    event, blobs = _v2_event()
    payload = cast(dict[str, object], _plain(event.payload))
    source = cast(dict[str, object], payload["source"])
    if mutation == "wrong_origin":
        source["content_origin"] = "original"
    elif mutation == "wrong_structure":
        source["structure_origin"] = "model_proposed"
    elif mutation == "missing_provenance":
        del source["generated_provenance"]
    else:
        source["markdown"] = "# forged inline content"

    forged = replace(event, payload=cast(JsonObject, payload))
    with pytest.raises((TypeError, ValueError)):
        decode_source_revision_event(forged, blobs.__getitem__)


def test_generated_v2_rejects_corrupt_blob_bytes() -> None:
    event, _blobs = _v2_event()

    with pytest.raises(ValueError, match=r"checksum|length"):
        decode_source_revision_event(event, lambda _ref: b"corrupt")


@pytest.mark.parametrize(
    "mutation",
    (
        {"causation_id": None},
        {"causation_id": EventId("event-different-accept")},
        {"session_id": SessionId("session-forged")},
        {"actor": Actor(PrincipalKind.HUMAN, "reviewer")},
    ),
)
def test_generated_v2_requires_service_unscoped_admission_event(
    mutation: dict[str, object],
) -> None:
    event, blobs = _v2_event()
    forged = replace(event, **mutation)

    with pytest.raises(ValueError, match=r"session|caus|caused|SERVICE"):
        decode_source_revision_event(forged, blobs.__getitem__)
