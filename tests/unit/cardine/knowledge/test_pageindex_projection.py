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
        candidate.content_sha256 == sha256(content.encode()).hexdigest()
        for candidate in candidates
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

    assert map_structural_tree(
        course_id="course",
        source_id="source",
        revision_id="revision",
        content=content,
        tree=tree,
    ) == ()
