"""Host-owned reconciliation of derived locators to immutable source spans.

No final unit IDs are materialized here: this repository has no unitizer owner.
Unreconciled indexes fail explicitly, without guessing or dropping source text.
"""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256

from study_agent.domain.document_index import (
    DocumentIndex,
    DocumentNode,
    LocatorKind,
    SourceLocator,
)
from study_agent.domain.source import SourceDocument
from study_agent.domain.substrate import Substrate
from study_agent.flashcards.planning import CanonicalSourceSpan


class LocatorReconciliationError(ValueError):
    """Derived navigation could not be grounded in the supplied canonical substrate."""


@dataclass(frozen=True, slots=True)
class DocumentIndexContext:
    source: SourceDocument
    substrate: Substrate
    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.source, SourceDocument) or not isinstance(self.substrate, Substrate):
            raise LocatorReconciliationError("context requires canonical source and substrate")
        if not isinstance(self.text, str):
            raise LocatorReconciliationError("substrate text must be Unicode text")
        encoded = self.text.encode("utf-8")
        digest = sha256(encoded).hexdigest()
        if (
            self.source.normalized_blob != self.substrate.blob
            or digest != self.substrate.blob.checksum_sha256
            or len(encoded) != self.substrate.blob.byte_length
            or len(self.text) != self.substrate.character_length
            or len(self.text) != self.source.normalized_character_length
            or self.source.normalization_version != self.substrate.normalization_version
        ):
            raise LocatorReconciliationError("source/substrate/text binding mismatch")


@dataclass(frozen=True, slots=True)
class DocumentCandidate:
    """A disjoint local portion; keys/path/summary are derived navigation only."""

    candidate_key: str
    node_key: str
    span: CanonicalSourceSpan
    title: str | None
    summary: str | None
    document_path: tuple[str, ...]
    ancestor_keys: tuple[str, ...]
    order: int


def validate_document_index(index: DocumentIndex, context: DocumentIndexContext) -> None:
    """Revalidate untrusted adapter results before constructing any candidate."""
    context.__post_init__()
    if not isinstance(index, DocumentIndex):
        raise LocatorReconciliationError("expected DocumentIndex")
    if not index.fingerprint:
        raise LocatorReconciliationError("adapter index has no fingerprint")
    try:
        index.__post_init__()
    except (TypeError, ValueError) as exc:
        raise LocatorReconciliationError(str(exc)) from exc
    if (
        index.source_id != context.source.source_id
        or index.revision_id != context.source.revision_id
        or index.substrate_id != context.substrate.substrate_id
    ):
        raise LocatorReconciliationError("index source/revision/substrate binding mismatch")


def resolve_locator(locator: SourceLocator, context: DocumentIndexContext) -> CanonicalSourceSpan:
    """Resolve inclusive pages/lines or half-open Unicode offsets; never infer gaps."""
    context.__post_init__()
    if not isinstance(locator, SourceLocator):
        raise LocatorReconciliationError("expected SourceLocator")
    try:
        locator.__post_init__()
    except (TypeError, ValueError) as exc:
        raise LocatorReconciliationError(str(exc)) from exc
    start: int
    end: int
    if locator.kind is LocatorKind.PDF_PAGE_RANGE:
        assert locator.start_page is not None and locator.end_page is not None
        page_count = context.substrate.page_count
        pages = {entry.page: entry.offset for entry in context.substrate.page_map}
        if (
            page_count is None
            or locator.end_page > page_count
            or any(page not in pages for page in range(locator.start_page, locator.end_page + 1))
        ):
            raise LocatorReconciliationError("requested PDF pages have no exact page map")
        start = pages[locator.start_page]
        if locator.end_page == page_count:
            end = len(context.text)
        else:
            following = locator.end_page + 1
            if following not in pages:
                raise LocatorReconciliationError("following PDF page has no exact boundary")
            end = pages[following]
        label = f"pages:{locator.start_page}-{locator.end_page}"
    elif locator.kind is LocatorKind.MARKDOWN_LINE_RANGE:
        assert locator.start_line is not None and locator.end_line is not None
        # A trailing newline terminates the final line; it does not create content.
        starts = [0] + [
            i + 1
            for i, char in enumerate(context.text)
            if char == "\n" and i + 1 < len(context.text)
        ]
        if locator.end_line > len(starts):
            raise LocatorReconciliationError("line locator exceeds substrate bounds")
        start = starts[locator.start_line - 1]
        end = starts[locator.end_line] if locator.end_line < len(starts) else len(context.text)
        label = f"lines:{locator.start_line}-{locator.end_line}"
    else:
        assert locator.start_offset is not None and locator.end_offset is not None
        start, end = locator.start_offset, locator.end_offset
        label = f"unicode:{start}-{end}"
    if not 0 <= start < end <= len(context.text):
        raise LocatorReconciliationError("resolved span exceeds substrate bounds")
    return CanonicalSourceSpan(
        context.source.source_id, context.source.revision_id, start, end, label
    )


def resolve_node_span(
    index: DocumentIndex, node: DocumentNode, context: DocumentIndexContext
) -> CanonicalSourceSpan:
    validate_document_index(index, context)
    if node not in index.nodes:
        raise LocatorReconciliationError("node is not part of supplied index")
    return resolve_locator(node.locator, context)


def candidate_nodes(
    index: DocumentIndex, context: DocumentIndexContext
) -> tuple[DocumentCandidate, ...]:
    """Partition node coverage into leaves and uncovered ancestor-local portions.

    Child spans must lie within parents and siblings must be disjoint and ordered.
    Every covered character is emitted once; ancestor summaries cannot supply local
    source evidence or duplicate the descendants' classification scope.
    """
    validate_document_index(index, context)
    by_key = {node.node_key: node for node in index.nodes}
    spans = {node.node_key: resolve_locator(node.locator, context) for node in index.nodes}
    root = next(node for node in index.nodes if node.parent_key is None)
    root_span = spans[root.node_key]
    if root_span.start_offset != 0 or root_span.end_offset != len(context.text):
        raise LocatorReconciliationError("root must cover the complete substrate")
    portions: list[tuple[DocumentNode, int, int]] = []
    for node in index.nodes:
        parent = spans[node.node_key]
        cursor = parent.start_offset
        for key in node.children:
            child = spans[key]
            if child.start_offset < cursor or child.end_offset > parent.end_offset:
                raise LocatorReconciliationError(
                    "child spans overlap, are unordered or escape parent"
                )
            if cursor < child.start_offset:
                portions.append((node, cursor, child.start_offset))
            cursor = child.end_offset
        if cursor < parent.end_offset:
            portions.append((node, cursor, parent.end_offset))
    result: list[DocumentCandidate] = []
    for order, (node, start, end) in enumerate(sorted(portions, key=lambda item: item[1])):
        path = [node]
        ancestor = node.parent_key
        while ancestor is not None:
            path.append(by_key[ancestor])
            ancestor = by_key[ancestor].parent_key
        path.reverse()
        span = CanonicalSourceSpan(
            index.source_id, index.revision_id, start, end, f"unicode:{start}-{end}"
        )
        result.append(
            DocumentCandidate(
                candidate_key=f"{node.node_key}@{start}:{end}",
                node_key=node.node_key,
                span=span,
                title=node.title,
                summary=node.summary if not node.children else None,
                document_path=tuple(item.title or item.node_key for item in path),
                ancestor_keys=tuple(item.node_key for item in path),
                order=order,
            )
        )
    return tuple(result)
