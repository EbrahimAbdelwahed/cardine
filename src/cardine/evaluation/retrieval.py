"""Needle coverage measurements; neither gold labels nor scores authorize answers."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256

from study_agent.domain import ChunkId, Citation, RevisionId, SourceId
from study_agent.ports.retrieval import (
    EvidenceStatus,
    RetrievalCatalogPort,
    RetrievalDocument,
    RetrievalEvidence,
    RetrievalPort,
    RetrievalQuery,
)


@dataclass(frozen=True)
class GoldSpan:
    source_id: SourceId
    revision_id: RevisionId
    start: int
    end: int
    checksum: str

    def validate(self, catalog: RetrievalCatalogPort) -> None:
        if not 0 <= self.start < self.end:
            raise ValueError("gold span must have positive exact bounds")
        documents = tuple(
            item for item in catalog.documents()
            if item.source_id == self.source_id and item.revision_id == self.revision_id
        )
        if _coverage(self, documents) != self.end - self.start:
            raise ValueError("gold span is not completely covered by canonical chunks")
        parts: list[str] = []
        cursor = self.start
        for document in sorted(documents, key=lambda item: item.chunk.start_offset):
            start, end = max(cursor, document.chunk.start_offset), min(
                self.end, document.chunk.end_offset,
            )
            if end <= start:
                continue
            resolved = catalog.resolve(Citation(
                self.source_id, self.revision_id, document.chunk.chunk_id,
                start, end, "audit-gold-validation",
            ))
            parts.append(resolved.text)
            cursor = end
        if sha256("".join(parts).encode()).hexdigest() != self.checksum:
            raise ValueError("gold span checksum differs from canonical text")


@dataclass(frozen=True)
class NeedleCase:
    case_id: str
    query: RetrievalQuery
    facets: Mapping[str, tuple[GoldSpan, ...]]
    alternatives: tuple[str, ...] = ()
    expected_empty: bool = False

    def validate(self, catalog: RetrievalCatalogPort) -> None:
        if (
            not self.case_id or type(self.expected_empty) is not bool
            or self.expected_empty == bool(self.facets)
        ):
            raise ValueError("case requires positive facets or an explicit negative label")
        eligible = tuple(item for item in catalog.documents() if _eligible(item, self.query))
        for name, spans in self.facets.items():
            if not name or not spans:
                raise ValueError("each facet requires at least one exact gold span")
            for span in spans:
                span.validate(catalog)
                if _coverage(span, eligible) != span.end - span.start:
                    raise ValueError("gold span lies outside the query's authorized scope")


@dataclass(frozen=True)
class SearchPlan:
    name: str
    climb: bool = False
    neighbor_radius: int = 0
    context_limit: int = 8

    def __post_init__(self) -> None:
        if type(self.climb) is not bool:
            raise ValueError("climb must be a boolean")
        if (
            not self.name or type(self.neighbor_radius) is not int
            or not 0 <= self.neighbor_radius <= 3
        ):
            raise ValueError("plan requires a name and a neighbor radius in [0, 3]")
        if type(self.context_limit) is not int or not 1 <= self.context_limit <= 100:
            raise ValueError("context limit must be in [1, 100]")


def measure_case(
    case: NeedleCase,
    plan: SearchPlan,
    retrieval: RetrievalPort,
    catalog: RetrievalCatalogPort,
) -> dict[str, object]:
    """Probe bounded queries and neighbors, then score only the capped read set.

    Alternatives are manually supplied diagnostic queries, not an autonomous
    production policy. Original-query results keep precedence in the context.
    """
    case.validate(catalog)
    queries = (case.query.text, *(case.alternatives if plan.climb else ()))
    if len(queries) > 4 or len(set(queries)) != len(queries):
        raise ValueError("at most four distinct diagnostic queries are allowed")
    searches = tuple(retrieval.search(replace(case.query, text=text)) for text in queries)
    seeds: list[RetrievalEvidence] = []
    seen: set[ChunkId] = set()
    for search in searches:
        for evidence in search.evidence:
            if evidence.chunk.chunk_id not in seen:
                seen.add(evidence.chunk.chunk_id)
                seeds.append(evidence)
    selected = seeds[:plan.context_limit]
    neighbors = {
        (item.source_id, item.revision_id, item.chunk.ordinal): item
        for item in catalog.documents() if _eligible(item, case.query)
    }
    # Search hits retain precedence. Expansion never crosses revision, policy,
    # or section boundaries, and every neighbor resolves to canonical bytes.
    for distance in range(1, plan.neighbor_radius + 1):
        for seed in seeds:
            for delta in (-distance, distance):
                document = neighbors.get((
                    seed.chunk.source_id, seed.chunk.revision_id, seed.chunk.ordinal + delta,
                ))
                if (
                    document is None or document.chunk.chunk_id in seen
                    or document.chunk.section_path != seed.chunk.section_path
                    or len(selected) >= plan.context_limit
                ):
                    continue
                resolved = catalog.resolve(Citation(
                    document.source_id, document.revision_id, document.chunk.chunk_id,
                    document.chunk.start_offset, document.chunk.end_offset, "audit-neighbor",
                ))
                if resolved.text != document.text:
                    raise ValueError("neighbor differs from canonical resolved evidence")
                selected.append(RetrievalEvidence(
                    document.chunk, resolved.citation, resolved.text, 0.0,
                ))
                seen.add(document.chunk.chunk_id)
    documents = tuple(catalog.canonical_document(item.chunk.chunk_id) for item in selected)
    facets = {
        name: all(_coverage(span, documents) == span.end - span.start for span in spans)
        for name, spans in case.facets.items()
    }
    complete = not selected if case.expected_empty else all(facets.values())
    return {
        "case_id": case.case_id,
        "plan": plan.name,
        "complete": complete,
        "facets": facets,
        "expected_empty": case.expected_empty,
        "false_sufficient": searches[0].status is EvidenceStatus.SUFFICIENT and not complete,
        "query_count": len(queries),
        "search_statuses": [result.status.value for result in searches],
        "context_chunks": len(selected),
        "context_characters": sum(len(item.text) for item in selected),
        "evidence": [
            {
                "source_id": str(item.chunk.source_id),
                "revision_id": str(item.chunk.revision_id),
                "chunk_id": str(item.chunk.chunk_id),
                "ordinal": item.chunk.ordinal,
                "start": item.chunk.start_offset,
                "end": item.chunk.end_offset,
            }
            for item in selected
        ],
    }


def _eligible(document: RetrievalDocument, query: RetrievalQuery) -> bool:
    return (
        document.course_id == query.course_id
        and (query.include_superseded or document.is_current_revision)
        and (not query.revision_ids or document.revision_id in query.revision_ids)
        and (not query.source_kinds or document.source_kind in query.source_kinds)
        and (not query.source_roles or document.source_role in query.source_roles)
        and document.trust_level >= query.minimum_trust_level
    )


def _coverage(span: GoldSpan, documents: Sequence[RetrievalDocument]) -> int:
    intervals = sorted(
        (max(span.start, item.chunk.start_offset), min(span.end, item.chunk.end_offset))
        for item in documents
        if item.source_id == span.source_id and item.revision_id == span.revision_id
        and item.chunk.start_offset < span.end and item.chunk.end_offset > span.start
    )
    covered = 0
    cursor = span.start
    for start, end in intervals:
        covered += max(0, end - max(start, cursor))
        cursor = max(cursor, end)
    return covered
