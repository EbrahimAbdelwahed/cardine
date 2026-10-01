from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ._validation import JsonObject, freeze_object, require_aware, require_text
from .identifiers import BlobId, ChunkId, RevisionId, SourceId
from .provenance import (
    ContentOrigin,
    DocumentConversionProvenance,
    GeneratedDocumentProvenance,
    StructureOrigin,
)


class SourceKind(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"


@dataclass(frozen=True, slots=True)
class BlobRef:
    id: BlobId
    checksum_sha256: str
    byte_length: int

    def __post_init__(self) -> None:
        if len(self.checksum_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.checksum_sha256
        ):
            raise ValueError("checksum_sha256 must be a lowercase SHA-256 hex digest")
        if self.byte_length < 0:
            raise ValueError("byte_length must be non-negative")


@dataclass(frozen=True, slots=True)
class SourceDocument:
    source_id: SourceId
    revision_id: RevisionId
    kind: SourceKind
    title: str
    media_type: str
    checksum_sha256: str
    byte_length: int
    created_at: datetime
    trust_level: int
    source_role: str
    blob: BlobRef
    normalized_blob: BlobRef
    normalization_version: str
    normalized_character_length: int
    structure_origin: StructureOrigin
    ingestion_method: str
    content_origin: ContentOrigin = ContentOrigin.ORIGINAL
    conversion_provenance: DocumentConversionProvenance | None = None
    generated_provenance: GeneratedDocumentProvenance | None = None

    def __post_init__(self) -> None:
        require_text(self.title, "title")
        require_text(self.media_type, "media_type")
        require_text(self.source_role, "source_role")
        require_text(self.ingestion_method, "ingestion_method")
        require_text(self.normalization_version, "normalization_version")
        if self.normalized_character_length < 1:
            raise ValueError("normalized_character_length must be positive")
        require_aware(self.created_at, "created_at")
        if self.checksum_sha256 != self.blob.checksum_sha256:
            raise ValueError("source checksum must match its immutable blob")
        if self.byte_length != self.blob.byte_length:
            raise ValueError("source byte_length must match its immutable blob")
        if str(self.blob.id) != f"sha256:{self.blob.checksum_sha256}":
            raise ValueError("source blob id must match its SHA-256 checksum")
        if str(self.normalized_blob.id) != f"sha256:{self.normalized_blob.checksum_sha256}":
            raise ValueError("normalized blob id must match its SHA-256 checksum")
        if not 0 <= self.trust_level <= 100:
            raise ValueError("trust_level must be between 0 and 100")
        if self.content_origin is ContentOrigin.EXTRACTED:
            if self.conversion_provenance is None:
                raise ValueError("extracted content requires conversion provenance")
            if self.generated_provenance is not None:
                raise ValueError("extracted content cannot carry generated provenance")
            if self.conversion_provenance.page_spans and (
                self.conversion_provenance.page_spans[-1].end_offset
                > self.normalized_character_length
            ):
                raise ValueError("conversion page span exceeds normalized content")
        elif self.content_origin is ContentOrigin.GENERATED:
            if self.generated_provenance is None:
                raise ValueError("generated content requires generated provenance")
            if self.conversion_provenance is not None:
                raise ValueError("generated content cannot carry conversion provenance")
        elif self.conversion_provenance is not None:
            raise ValueError("conversion provenance requires extracted content")
        elif self.generated_provenance is not None:
            raise ValueError("generated provenance requires generated content")


@dataclass(frozen=True, slots=True)
class SourceChunk:
    chunk_id: ChunkId
    source_id: SourceId
    revision_id: RevisionId
    start_offset: int
    end_offset: int
    section_path: tuple[str, ...]
    ordinal: int
    checksum_sha256: str
    chunker_version: str
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "section_path", tuple(self.section_path))
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("chunk offsets must describe a non-empty forward span")
        if self.ordinal < 0:
            raise ValueError("ordinal must be non-negative")
        if len(self.checksum_sha256) != 64 or any(
            char not in "0123456789abcdef" for char in self.checksum_sha256
        ):
            raise ValueError("checksum_sha256 must be a lowercase SHA-256 hex digest")
        require_text(self.chunker_version, "chunker_version")
        for section in self.section_path:
            require_text(section, "section_path item")
        object.__setattr__(self, "metadata", freeze_object(self.metadata))


@dataclass(frozen=True, slots=True)
class Citation:
    source_id: SourceId
    revision_id: RevisionId
    chunk_id: ChunkId
    start_offset: int
    end_offset: int
    locator: str
    quoted_snippet: str | None = None

    def __post_init__(self) -> None:
        if self.start_offset < 0 or self.end_offset <= self.start_offset:
            raise ValueError("citation offsets must describe a non-empty forward span")
        require_text(self.locator, "locator")
        if self.quoted_snippet is not None and not self.quoted_snippet:
            raise ValueError("quoted_snippet must be non-empty when supplied")


@dataclass(frozen=True, slots=True)
class ResolvedCitation:
    citation: Citation
    text: str

    def __post_init__(self) -> None:
        if not self.text:
            raise ValueError("resolved citation text must be non-empty")
        if self.citation.quoted_snippet is not None and self.citation.quoted_snippet != self.text:
            raise ValueError("resolved text must match the citation's quoted snippet")
