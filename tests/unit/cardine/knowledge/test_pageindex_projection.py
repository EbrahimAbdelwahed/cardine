from __future__ import annotations

from hashlib import sha256

import pytest

from cardine.knowledge import PageIndexStatus, map_structural_tree


def test_mapping_discards_ambiguous_text_and_keeps_unique_canonical_span() -> None:
    content = "# One\nRepeated\n# Two\nRepeated\n# Three\nUnique\n"
    tree = [
        {"node_id": "0001", "title": "One", "text": "# One\nRepeated", "line_num": 1},
        {"node_id": "0002", "title": "Repeated", "text": "Repeated", "line_num": 2},
        {"node_id": "0003", "title": "Three", "text": "# Three\nUnique", "line_num": 5},
    ]
    candidates = map_structural_tree(
        course_id="course",
        source_id="source",
        revision_id="revision",
        content=content,
        tree=tree,
    )
    assert [candidate.node_id for candidate in candidates] == ["0001", "0003"]
    assert all(
        candidate.content_sha256 == sha256(content.encode()).hexdigest() for candidate in candidates
    )


def test_projection_status_enum_is_closed() -> None:
    assert tuple(item.value for item in PageIndexStatus) == (
        "queued",
        "indexing",
        "ready",
        "degraded",
        "failed",
        "disabled",
    )
    with pytest.raises(ValueError):
        PageIndexStatus("unknown")


def test_mapping_degrades_instead_of_silently_truncating_more_than_256_nodes() -> None:
    content = "\n".join(f"# Heading {index}" for index in range(257))
    tree = [
        {
            "node_id": f"{index:04d}",
            "title": f"Heading {index}",
            "text": f"# Heading {index}",
            "line_num": index + 1,
        }
        for index in range(257)
    ]

    assert (
        map_structural_tree(
            course_id="course",
            source_id="source",
            revision_id="revision",
            content=content,
            tree=tree,
        )
        == ()
    )


def test_index_navigation_uses_canonical_line_offsets_not_provider_text() -> None:
    from cardine.adapters.document_index.pageindex import PageIndexDocumentIndexAdapter
    from cardine.knowledge.pageindex_projection import candidates_from_document_index
    from study_agent.domain import RevisionId, SourceId, SubstrateId
    from study_agent.ports.document_index import DocumentIndexRequest

    content = "Preamble.\n# One\n\u03b1 repeated\n# Two\n\u03b1 repeated\n"
    digest = sha256(content.encode()).hexdigest()
    request = DocumentIndexRequest(
        SourceId("source"),
        RevisionId("revision"),
        SubstrateId(f"substrate:sha256:{digest}"),
        "text/markdown",
        content.encode(),
        content,
    )
    index = PageIndexDocumentIndexAdapter().build_sync(request)
    candidates = candidates_from_document_index(
        course_id="course",
        source_id="source",
        revision_id="revision",
        content=content,
        index=index,
    )
    assert len(candidates) == 2
    assert [content[item.start_offset : item.end_offset] for item in candidates] == [
        "# One\n\u03b1 repeated\n",
        "# Two\n\u03b1 repeated\n",
    ]
    assert all(item.node_id != "document-root" for item in candidates)


def test_index_navigation_rejects_unknown_substrate_and_outside_line_bounds() -> None:
    from cardine.knowledge.pageindex_projection import candidates_from_document_index
    from study_agent.domain import RevisionId, SourceId, SubstrateId
    from study_agent.domain.document_index import (
        DocumentIndex,
        DocumentNode,
        LocatorKind,
        SourceLocator,
    )

    content = "Canonical\n"
    digest = sha256(content.encode()).hexdigest()
    index = DocumentIndex(
        SourceId("source"),
        RevisionId("revision"),
        SubstrateId(f"substrate:sha256:{digest}"),
        "index-v1",
        "provider",
        "1",
        "config",
        (
            DocumentNode(
                "root",
                None,
                None,
                None,
                SourceLocator(
                    LocatorKind.MARKDOWN_LINE_RANGE,
                    start_line=1,
                    end_line=2,
                ),
                0,
            ),
        ),
    )
    with pytest.raises(ValueError, match="outside canonical"):
        candidates_from_document_index(
            course_id="course",
            source_id="source",
            revision_id="revision",
            content=content,
            index=index,
        )
    with pytest.raises(ValueError, match="binding mismatch"):
        candidates_from_document_index(
            course_id="course",
            source_id="other",
            revision_id="revision",
            content=content,
            index=index,
        )
