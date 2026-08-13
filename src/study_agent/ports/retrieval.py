from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from study_agent.domain._validation import require_text
from study_agent.domain.identifiers import ChunkId, CourseId, RevisionId, SourceId
from study_agent.domain.source import Citation, ResolvedCitation, SourceChunk, SourceKind

MAX_RETRIEVAL_QUERY_CHARS = 512
MAX_RETRIEVAL_LIMIT = 100


class EvidenceStatus(StrEnum):
    SUFFICIENT = "sufficient"
    INSUFFICIENT = "insufficient"
    CONFLICTING = "conflicting"


@dataclass(frozen=True, slots=True)
class RetrievalDocument:
    course_id: CourseId
    source_id: SourceId
    revision_id: RevisionId
    chunk: SourceChunk
    text: str
    title: str
    source_kind: SourceKind
    source_role: str
    trust_level: int
    is_current_revision: bool

    def __post_init__(self) -> None:
        require_text(self.text, "retrieval document text")
        require_text(self.title, "retrieval document title")
        require_text(self.source_role, "retrieval document source_role")
        if self.chunk.source_id != self.source_id or self.chunk.revision_id != self.revision_id:
            raise ValueError("retrieval document chunk must belong to its source revision")
        if len(self.text) != self.chunk.end_offset - self.chunk.start_offset:
            raise ValueError("retrieval document text must fill its exact chunk span")
        if not 0 <= self.trust_level <= 100:
            raise ValueError("trust_level must be between 0 and 100")


@dataclass(frozen=True, slots=True)
class RetrievalQuery:
    course_id: CourseId
    text: str
    limit: int = 8
    revision_ids: tuple[RevisionId, ...] = ()
    minimum_trust_level: int = 0
    source_kinds: tuple[SourceKind, ...] = ()
    source_roles: tuple[str, ...] = ()
    include_superseded: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "revision_ids", tuple(self.revision_ids))
        object.__setattr__(self, "source_kinds", tuple(self.source_kinds))
        object.__setattr__(self, "source_roles", tuple(self.source_roles))
        if not self.text.strip():
            raise ValueError("text must be non-empty")
        if len(self.text) > MAX_RETRIEVAL_QUERY_CHARS:
            raise ValueError("text exceeds the retrieval query limit")
        if not 1 <= self.limit <= MAX_RETRIEVAL_LIMIT:
            raise ValueError("limit must be between 1 and 100")
        if not 0 <= self.minimum_trust_level <= 100:
            raise ValueError("minimum_trust_level must be between 0 and 100")
        for name, values in (
            ("revision_ids", self.revision_ids),
            ("source_kinds", self.source_kinds),
            ("source_roles", self.source_roles),
        ):
            if len(set(values)) != len(values):
                raise ValueError(f"{name} must not contain duplicates")
        for role in self.source_roles:
            require_text(role, "source role filter")


@dataclass(frozen=True, slots=True)
class RetrievalEvidence:
    chunk: SourceChunk
    citation: Citation
    text: str
    score: float

    def __post_init__(self) -> None:
        require_text(self.text, "retrieval evidence text")
        if not 0 <= self.score <= 1:
            raise ValueError("retrieval evidence score must be between 0 and 1")
        if self.citation.chunk_id != self.chunk.chunk_id:
            raise ValueError("retrieval evidence citation must identify its chunk")
        if (
            self.citation.source_id != self.chunk.source_id
            or self.citation.revision_id != self.chunk.revision_id
        ):
            raise ValueError("retrieval evidence citation must belong to its chunk revision")
        if (
            self.citation.start_offset != self.chunk.start_offset
            or self.citation.end_offset != self.chunk.end_offset
        ):
            raise ValueError("retrieval evidence citation must cover its complete chunk")
        if self.citation.quoted_snippet != self.text:
            raise ValueError("retrieval evidence text must be the canonical quoted snippet")


@dataclass(frozen=True, slots=True)
class RetrievalEvidenceSet:
    status: EvidenceStatus
    evidence: tuple[RetrievalEvidence, ...]
    query_fingerprint: str
    strategy_id: str
    strategy_version: str
    index_version: str
    read_set_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence", tuple(self.evidence))
        _require_fingerprint(self.query_fingerprint, "query_fingerprint")
        require_text(self.strategy_id, "strategy_id")
        require_text(self.strategy_version, "strategy_version")
        require_text(self.index_version, "index_version")
        _require_fingerprint(self.read_set_fingerprint, "read_set_fingerprint")
        if self.read_set_fingerprint != retrieval_read_set_fingerprint(self.evidence):
            raise ValueError("read_set_fingerprint must commit to the ordered evidence")
        if self.status is EvidenceStatus.INSUFFICIENT and self.evidence:
            raise ValueError("insufficient evidence sets must be empty")
        if (
            self.status in {EvidenceStatus.SUFFICIENT, EvidenceStatus.CONFLICTING}
            and not self.evidence
        ):
            raise ValueError(f"{self.status.value} evidence sets must be non-empty")


@dataclass(frozen=True, slots=True)
class IndexReceipt:
    indexed_chunks: int
    index_version: str
    catalog_fingerprint: str

    def __post_init__(self) -> None:
        if self.indexed_chunks < 0:
            raise ValueError("indexed_chunks must be non-negative")
        require_text(self.index_version, "index_version")
        _require_fingerprint(self.catalog_fingerprint, "catalog_fingerprint")


class RetrievalCatalogPort(Protocol):
    def documents(self, *, include_superseded: bool = False) -> Sequence[RetrievalDocument]: ...

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument: ...

    def resolve(self, citation: Citation) -> ResolvedCitation: ...


class RetrievalPort(Protocol):
    def index(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt: ...

    def search(self, query: RetrievalQuery) -> RetrievalEvidenceSet: ...


def retrieval_read_set_fingerprint(
    evidence: Sequence[RetrievalEvidence],
) -> str:
    """Commit to ordered canonical retrieval candidates without adapter metadata."""

    payload = json.dumps(
        [
            {
                "chunk_id": str(item.chunk.chunk_id),
                "source_id": str(item.chunk.source_id),
                "revision_id": str(item.chunk.revision_id),
                "start_offset": item.citation.start_offset,
                "end_offset": item.citation.end_offset,
                "checksum_sha256": item.chunk.checksum_sha256,
                "score": item.score,
            }
            for item in evidence
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(b"study-agent-retrieval-read-set-v1\0" + payload).hexdigest()


def retrieval_catalog_fingerprint(
    documents: Sequence[RetrievalDocument],
) -> str:
    """Commit to the exact canonical catalog independent of adapter storage."""

    ordered = sorted(documents, key=lambda item: str(item.chunk.chunk_id))
    chunk_ids = tuple(item.chunk.chunk_id for item in ordered)
    if len(set(chunk_ids)) != len(chunk_ids):
        raise ValueError("retrieval catalog must not contain duplicate chunk ids")
    payload = json.dumps(
        [
            {
                "course_id": str(item.course_id),
                "source_id": str(item.source_id),
                "revision_id": str(item.revision_id),
                "chunk": {
                    "chunk_id": str(item.chunk.chunk_id),
                    "source_id": str(item.chunk.source_id),
                    "revision_id": str(item.chunk.revision_id),
                    "start_offset": item.chunk.start_offset,
                    "end_offset": item.chunk.end_offset,
                    "section_path": item.chunk.section_path,
                    "ordinal": item.chunk.ordinal,
                    "checksum_sha256": item.chunk.checksum_sha256,
                    "chunker_version": item.chunk.chunker_version,
                    "metadata": dict(item.chunk.metadata),
                },
                "text": item.text,
                "title": item.title,
                "source_kind": item.source_kind.value,
                "source_role": item.source_role,
                "trust_level": item.trust_level,
                "is_current_revision": item.is_current_revision,
            }
            for item in ordered
        ],
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return sha256(b"study-agent-retrieval-catalog-v1\0" + payload).hexdigest()


def _require_fingerprint(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")
