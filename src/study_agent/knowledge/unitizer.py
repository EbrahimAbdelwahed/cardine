"""Bind identity-free document drafts to existing immutable canonical chunks.

Cardine's canonical ChunkId owner remains ingestion.identity.chunk_id_for.
Derived index nodes cannot create chunks, UnitIds or canonical events. This
projection preserves historical chunk identity while producing exact citations
for the portion of each immutable chunk contained in a structural draft.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.source import Citation, SourceChunk
from study_agent.ingestion.identity import chunk_id_for
from study_agent.knowledge.document_index import (
    DocumentCandidate,
    DocumentIndexContext,
    LocatorReconciliationError,
    candidate_nodes,
    verified_node_spans,
)


@dataclass(frozen=True, slots=True)
class MaterializedDocumentUnit:
    draft: DocumentCandidate
    citations: tuple[Citation, ...]


def draft_units_from_document_index(
    index: DocumentIndex, context: DocumentIndexContext
) -> tuple[DocumentCandidate, ...]:
    return candidate_nodes(index, context)


def materialize_unit_drafts(
    context: DocumentIndexContext,
    drafts: tuple[DocumentCandidate, ...],
    canonical_chunks: tuple[SourceChunk, ...],
) -> tuple[MaterializedDocumentUnit, ...]:
    """Intersect validated source drafts with immutable host-supplied chunks.

    The host supplies its canonical revision's chunk set. Chunk identities and
    bytes are verified using the existing authority. Non-whitespace gaps fail;
    historical chunker-trimmed whitespace is permitted, without inventing a new
    canonical identity for it. No summaries or index keys become citation data.
    """
    context.__post_init__()
    _validate_canonical_chunks(context, canonical_chunks)
    units: list[MaterializedDocumentUnit] = []
    previous_end = 0
    draft_keys: set[str] = set()
    for draft in drafts:
        if not isinstance(draft, DocumentCandidate):
            raise LocatorReconciliationError("draft must be DocumentCandidate")
        span = draft.span
        span.__post_init__()
        if (
            span.source_id != context.source.source_id
            or span.revision_id != context.source.revision_id
            or span.start_offset < previous_end
            or span.end_offset > len(context.text)
            or draft.candidate_key in draft_keys
        ):
            raise LocatorReconciliationError("draft source binding or ordering mismatch")
        citations: list[Citation] = []
        cursor = span.start_offset
        for chunk in canonical_chunks:
            start = max(span.start_offset, chunk.start_offset)
            end = min(span.end_offset, chunk.end_offset)
            if start >= end:
                continue
            if context.text[cursor:start].strip():
                raise LocatorReconciliationError(
                    "draft contains source text without canonical chunk"
                )
            citations.append(
                Citation(
                    source_id=chunk.source_id,
                    revision_id=chunk.revision_id,
                    chunk_id=chunk.chunk_id,
                    start_offset=start,
                    end_offset=end,
                    locator=f"unicode:{start}-{end}",
                )
            )
            cursor = end
        if context.text[cursor : span.end_offset].strip():
            raise LocatorReconciliationError("draft contains source text without canonical chunk")
        units.append(MaterializedDocumentUnit(draft, tuple(citations)))
        previous_end = span.end_offset
        draft_keys.add(draft.candidate_key)
    return tuple(units)


def _validate_canonical_chunks(
    context: DocumentIndexContext, canonical_chunks: tuple[SourceChunk, ...]
) -> None:
    previous_end = 0
    seen_ids: set[str] = set()
    for ordinal, chunk in enumerate(canonical_chunks):
        if not isinstance(chunk, SourceChunk):
            raise LocatorReconciliationError("canonical chunks must be SourceChunk values")
        chunk.__post_init__()
        if (
            chunk.source_id != context.source.source_id
            or chunk.revision_id != context.source.revision_id
            or chunk.ordinal != ordinal
            or chunk.start_offset < previous_end
            or chunk.end_offset > len(context.text)
            or str(chunk.chunk_id) in seen_ids
        ):
            raise LocatorReconciliationError("canonical chunk binding or ordering mismatch")
        if context.text[previous_end : chunk.start_offset].strip():
            raise LocatorReconciliationError("source text without canonical chunk")
        digest = sha256(context.text[chunk.start_offset : chunk.end_offset].encode()).hexdigest()
        expected_id = chunk_id_for(
            source_id=chunk.source_id,
            revision_id=chunk.revision_id,
            start_offset=chunk.start_offset,
            end_offset=chunk.end_offset,
            checksum_sha256=digest,
            chunker_version=chunk.chunker_version,
        )
        if chunk.checksum_sha256 != digest or chunk.chunk_id != expected_id:
            raise LocatorReconciliationError("canonical chunk digest or identity mismatch")
        previous_end = chunk.end_offset
        seen_ids.add(str(chunk.chunk_id))
    if context.text[previous_end:].strip():
        raise LocatorReconciliationError("source text without canonical chunk")


def candidates_for_canonical_chunks(
    index: DocumentIndex,
    context: DocumentIndexContext,
    chunks: tuple[SourceChunk, ...],
    *,
    selected_chunks: tuple[SourceChunk, ...] | None = None,
) -> tuple[DocumentCandidate, ...]:
    """Classify each existing whole chunk once, using PageIndex navigation only.

    Artifact commitments and worker evidence use full immutable chunks. A chunk
    crossing structural nodes is assigned to their containing ancestor, rather
    than classifying slices and subsequently reintroducing excluded text.
    """
    from study_agent.flashcards.planning import CanonicalSourceSpan

    spans = verified_node_spans(index, context)
    _validate_canonical_chunks(context, chunks)
    if selected_chunks is not None:
        canonical = {chunk.chunk_id: chunk for chunk in chunks}
        if any(canonical.get(chunk.chunk_id) != chunk for chunk in selected_chunks):
            raise LocatorReconciliationError("selected chunks are not canonical")
        selected_ids = {chunk.chunk_id for chunk in selected_chunks}
        chunks = tuple(chunk for chunk in chunks if chunk.chunk_id in selected_ids)
    by_key = {node.node_key: node for node in index.nodes}
    paths: dict[str, tuple[str, ...]] = {}
    for node in index.nodes:
        path = [node.node_key]
        parent = node.parent_key
        while parent is not None:
            path.append(parent)
            parent = by_key[parent].parent_key
        paths[node.node_key] = tuple(reversed(path))
    candidates: list[DocumentCandidate] = []
    for chunk in chunks:
        containing = [
            node
            for node in index.nodes
            if spans[node.node_key].start_offset <= chunk.start_offset
            and spans[node.node_key].end_offset >= chunk.end_offset
        ]
        owner = max(containing, key=lambda node: len(paths[node.node_key]))
        owner_path = paths[owner.node_key]
        owner_span = spans[owner.node_key]
        exact = (
            owner_span.start_offset == chunk.start_offset
            and owner_span.end_offset == chunk.end_offset
        )
        candidates.append(
            DocumentCandidate(
                candidate_key=f"canonical-chunk:{chunk.chunk_id}",
                node_key=owner.node_key,
                span=CanonicalSourceSpan(
                    chunk.source_id,
                    chunk.revision_id,
                    chunk.start_offset,
                    chunk.end_offset,
                    f"unicode:{chunk.start_offset}-{chunk.end_offset}",
                ),
                title=owner.title,
                summary=owner.summary if exact else None,
                document_path=tuple(by_key[key].title or key for key in owner_path),
                ancestor_keys=owner_path,
                order=chunk.ordinal,
            )
        )
    return tuple(candidates)
