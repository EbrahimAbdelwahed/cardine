"""Canonical v0.1 ingestion policy and deterministic identity helpers."""

from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256

from study_agent.domain._validation import JsonObject
from study_agent.domain.artifact import LessonMaterialVariant
from study_agent.domain.identifiers import (
    ArtifactRevisionId,
    ChunkId,
    CourseId,
    EventId,
    RevisionId,
    RunId,
    SourceId,
)
from study_agent.domain.source import SourceKind
from study_agent.state import canonical_json_bytes

NORMALIZATION_POLICY_VERSION = "utf8-newlines-nfc-v1"
CHUNKER_POLICY_VERSION = "heading-paragraph-v1"
CHUNK_MAX_CHARACTERS = 1200
TEXT_MEDIA_TYPE = "text/plain"
MARKDOWN_MEDIA_TYPE = "text/markdown"
TEXT_INGESTION_METHOD = "utf8-text-v1"
MARKDOWN_INGESTION_METHOD = "utf8-markdown-v1"
GENERATED_MARKDOWN_INGESTION_METHOD = "generated-markdown-v1"


def source_kind_contract(kind: SourceKind) -> tuple[str, str]:
    if kind is SourceKind.TEXT:
        return TEXT_MEDIA_TYPE, TEXT_INGESTION_METHOD
    return MARKDOWN_MEDIA_TYPE, MARKDOWN_INGESTION_METHOD


def revision_id_for(
    *,
    original_sha256: str,
    source_id: SourceId,
    kind: SourceKind,
    title: str,
    trust_level: int,
    source_role: str,
    normalization_version: str,
    chunker_version: str,
    max_characters: int,
) -> RevisionId:
    """Identify immutable content, metadata, and processing configuration (v2)."""

    identity = b"study-agent-source-revision-v2\0" + canonical_json_bytes(
        {
            "chunker_version": chunker_version,
            "kind": kind.value,
            "max_characters": max_characters,
            "normalization_version": normalization_version,
            "original_sha256": original_sha256,
            "source_id": str(source_id),
            "source_role": source_role,
            "title": title,
            "trust_level": trust_level,
        }
    )
    return RevisionId(f"revision-sha256:{sha256(identity).hexdigest()}")


def legacy_revision_id_for(
    *,
    original_sha256: str,
    source_id: SourceId,
    kind: SourceKind,
    normalization_version: str,
    chunker_version: str,
    max_characters: int,
) -> RevisionId:
    """Reconstruct the v0.1 identity used before metadata became revision-bearing."""

    identity = (
        f"{source_id}\0{original_sha256}\0{kind.value}\0{normalization_version}\0"
        f"{chunker_version}\0{max_characters}"
    ).encode()
    return RevisionId(f"revision-sha256:{sha256(identity).hexdigest()}")


def chunk_id_for(
    *,
    source_id: SourceId,
    revision_id: RevisionId,
    start_offset: int,
    end_offset: int,
    checksum_sha256: str,
    chunker_version: str,
) -> ChunkId:
    identity = (
        f"{source_id}\0{revision_id}\0{start_offset}\0{end_offset}\0"
        f"{checksum_sha256}\0{chunker_version}"
    ).encode()
    return ChunkId(f"chunk-sha256:{sha256(identity).hexdigest()}")


def source_event_id_for(course_id: CourseId, revision_id: RevisionId) -> EventId:
    identity = f"{course_id}\0{revision_id}".encode()
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


def generated_source_id_for(
    *,
    course_id: CourseId,
    root_source_id: SourceId,
    root_revision_id: RevisionId,
    artifact_revision_id: ArtifactRevisionId,
    material_run_id: RunId,
    variant: LessonMaterialVariant,
) -> SourceId:
    """Derive a generated source identity from its complete lineage."""

    if not all(
        isinstance(value, expected)
        for value, expected in (
            (course_id, CourseId),
            (root_source_id, SourceId),
            (root_revision_id, RevisionId),
            (artifact_revision_id, ArtifactRevisionId),
            (material_run_id, RunId),
            (variant, LessonMaterialVariant),
        )
    ):
        raise TypeError("generated source identity inputs are not typed")
    identity = b"cardine-generated-source@1\0" + canonical_json_bytes(
        {
            "artifact_revision_id": str(artifact_revision_id),
            "course_id": str(course_id),
            "material_run_id": str(material_run_id),
            "root_revision_id": str(root_revision_id),
            "root_source_id": str(root_source_id),
            "variant": variant.value,
        }
    )
    return SourceId(f"generated-source-sha256:{sha256(identity).hexdigest()}")


def generated_revision_id_for(
    *,
    source_id: SourceId,
    root_source_id: SourceId,
    root_revision_id: RevisionId,
    artifact_revision_id: ArtifactRevisionId,
    material_run_id: RunId,
    variant: LessonMaterialVariant,
    markdown_sha256: str,
    title: str,
    normalization_version: str,
    chunker_version: str,
    max_characters: int,
    trust_level: int,
    source_role: str,
    root_normalized_blob_sha256: str,
    artifact_provenance_sha256: str,
    direct_parent_blob_sha256: str,
    human_decision_event_id: EventId,
    human_decision_at: datetime,
) -> RevisionId:
    """Derive the immutable revision identity for generated Markdown."""

    if not isinstance(source_id, SourceId):
        raise TypeError("generated revision requires SourceId")
    if len(markdown_sha256) != 64 or any(c not in "0123456789abcdef" for c in markdown_sha256):
        raise ValueError("markdown_sha256 must be lowercase SHA-256")
    approval_identity: JsonObject = {
        "artifact_provenance_sha256": artifact_provenance_sha256,
        "direct_parent_blob_sha256": direct_parent_blob_sha256,
        "human_decision_at": human_decision_at.astimezone(UTC)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z"),
        "human_decision_event_id": str(human_decision_event_id),
        "root_normalized_blob_sha256": root_normalized_blob_sha256,
        "source_role": source_role,
        "trust_level": trust_level,
    }
    identity = b"cardine-generated-revision@1\0" + canonical_json_bytes(
        {
            "artifact_revision_id": str(artifact_revision_id),
            "chunker_version": chunker_version,
            "max_characters": max_characters,
            "markdown_sha256": markdown_sha256,
            "material_run_id": str(material_run_id),
            "normalization_version": normalization_version,
            "root_revision_id": str(root_revision_id),
            "root_source_id": str(root_source_id),
            "source_id": str(source_id),
            "title": title,
            "variant": variant.value,
            "approval_identity": approval_identity,
        }
    )
    return RevisionId(f"generated-revision-sha256:{sha256(identity).hexdigest()}")


def generated_source_event_id_for(
    course_id: CourseId, source_id: SourceId, revision_id: RevisionId
) -> EventId:
    identity = b"cardine-generated-source-event@1\0" + canonical_json_bytes(
        {
            "course_id": str(course_id),
            "revision_id": str(revision_id),
            "source_id": str(source_id),
        }
    )
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


def source_revision_selected_event_id_for(
    course_id: CourseId,
    source_id: SourceId,
    revision_id: RevisionId,
    course_sequence: int,
) -> EventId:
    """Identify a current-revision transition at one canonical stream position."""

    identity = b"study-agent-source-revision-selected-v1\0" + canonical_json_bytes(
        {
            "course_id": str(course_id),
            "course_sequence": course_sequence,
            "revision_id": str(revision_id),
            "source_id": str(source_id),
        }
    )
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")
