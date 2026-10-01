"""Shared mechanical text preparation primitives for canonical ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from study_agent.domain.identifiers import BlobId, RevisionId, SourceId
from study_agent.domain.source import BlobRef, SourceChunk, SourceKind
from study_agent.ports import BlobStore

from .chunking import ChunkingConfig, chunk_text
from .normalization import NormalizedText, normalize_utf8


@dataclass(frozen=True, slots=True)
class PreparedText:
    original: bytes
    normalized: NormalizedText
    original_blob: BlobRef
    normalized_blob: BlobRef


def prepare_text(content: bytes) -> PreparedText:
    if not isinstance(content, bytes):
        raise TypeError("source content must be bytes")
    normalized = normalize_utf8(content)
    return PreparedText(
        content,
        normalized,
        predicted_blob(content),
        predicted_blob(normalized.content),
    )


def prepare_chunks(
    text: str,
    *,
    source_id: SourceId,
    revision_id: RevisionId,
    kind: SourceKind,
    config: ChunkingConfig,
) -> tuple[SourceChunk, ...]:
    return chunk_text(
        text,
        source_id=source_id,
        revision_id=revision_id,
        kind=kind,
        config=config,
    )


def predicted_blob(content: bytes) -> BlobRef:
    digest = sha256(content).hexdigest()
    return BlobRef(BlobId(f"sha256:{digest}"), digest, len(content))


def write_expected_blob(store: BlobStore, content: bytes, expected: BlobRef) -> None:
    actual = store.put(content)
    if actual != expected:
        raise ValueError("blob store returned a reference that does not match content")


__all__ = [
    "PreparedText",
    "predicted_blob",
    "prepare_chunks",
    "prepare_text",
    "write_expected_blob",
]
