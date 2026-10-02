from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from hashlib import sha256

import pytest

from study_agent.domain.document_index import (
    DocumentIndex,
    DocumentNode,
    LocatorKind,
    SourceLocator,
)
from study_agent.domain.identifiers import BlobId, RevisionId, SourceId, substrate_id_for
from study_agent.domain.provenance import StructureOrigin
from study_agent.domain.source import BlobRef, SourceDocument, SourceKind
from study_agent.domain.substrate import PageMapEntry, Substrate
from study_agent.knowledge.document_index import (
    DocumentIndexContext,
    LocatorReconciliationError,
    candidate_nodes,
    resolve_locator,
    resolve_node_span,
)
from study_agent.ports.document_index import DocumentIndexRequest

TEXT = "Café\nValves\nHeart"


def context(
    *, text: str = TEXT, page_count: int | None = None, page_map: tuple[PageMapEntry, ...] = ()
) -> DocumentIndexContext:
    data = text.encode()
    digest = sha256(data).hexdigest()
    blob = BlobRef(BlobId(f"sha256:{digest}"), digest, len(data))
    source = SourceDocument(
        SourceId("source"),
        RevisionId("revision"),
        SourceKind.MARKDOWN,
        "Anatomy",
        "text/markdown",
        digest,
        len(data),
        datetime(2026, 1, 1, tzinfo=UTC),
        100,
        "primary",
        blob,
        blob,
        "normalization@1",
        len(text),
        StructureOrigin.SOURCE_AUTHORED,
        "fixture",
    )
    substrate = Substrate(
        substrate_id_for(data), blob, len(text), "normalization@1", page_count, page_map
    )
    return DocumentIndexContext(source, substrate, text)


def locator(start: int, end: int) -> SourceLocator:
    return SourceLocator(LocatorKind.TEXT_SPAN, start_offset=start, end_offset=end)


def node(
    key: str = "root",
    *,
    parent: str | None = None,
    order: int = 0,
    start: int = 0,
    end: int = len(TEXT),
    children: tuple[str, ...] = (),
) -> DocumentNode:
    return DocumentNode(key, parent, key, "Derived summary", locator(start, end), order, children)


def index(nodes: tuple[DocumentNode, ...] | None = None) -> DocumentIndex:
    binding = context()
    return DocumentIndex(
        binding.source.source_id,
        binding.source.revision_id,
        binding.substrate.substrate_id,
        "index@1",
        "fixture",
        "1",
        "configuration@1",
        nodes or (node(),),
    )


def test_fingerprint_covers_all_index_fields_and_supplied_digest_is_verified() -> None:
    original = index()
    for changed in (
        replace(original, revision_id=RevisionId("other"), fingerprint=""),
        replace(original, producer_version="2", fingerprint=""),
        replace(original, config_fingerprint="changed", fingerprint=""),
        replace(original, nodes=(replace(node(), summary="Different"),), fingerprint=""),
        replace(original, nodes=(replace(node(), locator=locator(1, len(TEXT))),), fingerprint=""),
    ):
        assert changed.fingerprint != original.fingerprint
    with pytest.raises(ValueError, match="fingerprint"):
        replace(original, producer_version="2")
    assert index().fingerprint == original.fingerprint


@pytest.mark.parametrize(
    "nodes",
    [
        (node(), node()),
        (node(), node("other", order=1)),
        (node(children=("missing",)),),
        (node(), node("child", parent="missing", order=1)),
        (node(children=("child",)), node("child", parent="root", order=0)),
        (
            node(),
            node("a", parent="b", order=1, children=("b",)),
            node("b", parent="a", order=2, children=("a",)),
        ),
    ],
)
def test_malformed_trees_rejected(nodes: tuple[DocumentNode, ...]) -> None:
    with pytest.raises(ValueError):
        index(nodes)


def test_exact_unicode_text_and_line_resolution() -> None:
    binding = context()
    assert resolve_locator(locator(0, 4), binding).end_offset == 4
    lines = SourceLocator(LocatorKind.MARKDOWN_LINE_RANGE, start_line=2, end_line=3)
    span = resolve_locator(lines, binding)
    assert binding.text[span.start_offset : span.end_offset] == "Valves\nHeart"
    with pytest.raises(LocatorReconciliationError):
        resolve_locator(
            SourceLocator(LocatorKind.MARKDOWN_LINE_RANGE, start_line=4, end_line=4), binding
        )
    with pytest.raises(LocatorReconciliationError):
        resolve_locator(locator(0, len(TEXT) + 1), binding)


def test_page_map_final_page_and_missing_boundaries_never_guessed() -> None:
    binding = context(
        page_count=3, page_map=(PageMapEntry(0, 1), PageMapEntry(5, 2), PageMapEntry(12, 3))
    )
    pages = SourceLocator(LocatorKind.PDF_PAGE_RANGE, start_page=2, end_page=3)
    span = resolve_locator(pages, binding)
    assert (span.start_offset, span.end_offset) == (5, len(TEXT))
    sparse = context(page_count=3, page_map=(PageMapEntry(0, 1), PageMapEntry(12, 3)))
    for start, end in ((1, 1), (1, 3), (2, 3)):
        with pytest.raises(LocatorReconciliationError):
            resolve_locator(
                SourceLocator(LocatorKind.PDF_PAGE_RANGE, start_page=start, end_page=end), sparse
            )


def test_candidate_partition_covers_every_character_once_and_preserves_paths() -> None:
    derived = index(
        (node(children=("child",)), node("child", parent="root", order=1, start=5, end=12))
    )
    candidates = candidate_nodes(derived, context())
    assert [(c.span.start_offset, c.span.end_offset) for c in candidates] == [
        (0, 5),
        (5, 12),
        (12, len(TEXT)),
    ]
    assert len({c.candidate_key for c in candidates}) == 3
    assert candidates[1].ancestor_keys == ("root", "child")
    assert candidates[1].document_path == ("root", "child")
    assert candidates[0].summary is None
    assert candidates[1].summary == "Derived summary"
    assert "".join(TEXT[c.span.start_offset : c.span.end_offset] for c in candidates) == TEXT


def test_overlapping_children_and_incomplete_root_fail_explicitly() -> None:
    derived = index(
        (
            node(children=("a", "b")),
            node("a", parent="root", order=1, start=2, end=9),
            node("b", parent="root", order=2, start=8, end=12),
        )
    )
    with pytest.raises(LocatorReconciliationError, match="overlap"):
        candidate_nodes(derived, context())
    with pytest.raises(LocatorReconciliationError, match="complete"):
        candidate_nodes(index((node(start=1),)), context())


def test_source_revision_digest_and_adapter_tampering_rejected() -> None:
    binding = context()
    with pytest.raises(LocatorReconciliationError, match="binding"):
        replace(binding, text="x" * len(TEXT))
    with pytest.raises(LocatorReconciliationError, match="binding"):
        candidate_nodes(replace(index(), revision_id=RevisionId("other"), fingerprint=""), binding)
    bad = index()
    object.__setattr__(bad, "producer_id", "tampered")
    with pytest.raises(LocatorReconciliationError, match="fingerprint"):
        candidate_nodes(bad, binding)
    with pytest.raises(LocatorReconciliationError, match="not part"):
        resolve_node_span(index(), node("unknown"), binding)


def test_request_verifies_immutable_normalized_substrate_binding() -> None:
    binding = context()
    valid = DocumentIndexRequest(
        binding.source.source_id,
        binding.source.revision_id,
        binding.substrate.substrate_id,
        "text/markdown",
        TEXT.encode(),
        TEXT,
    )
    with pytest.raises(ValueError, match="binding"):
        replace(valid, normalized_text="Different")


@pytest.mark.parametrize(
    "kwargs",
    [
        {"start_page": 1, "end_page": 0},
        {"start_page": True, "end_page": 2},
        {"start_page": 1, "end_page": 2, "start_line": 1},
    ],
)
def test_locator_rejects_bad_shapes(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        SourceLocator(LocatorKind.PDF_PAGE_RANGE, **kwargs)
