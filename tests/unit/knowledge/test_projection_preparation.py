"""Source-free regressions for projection cost and full-revision integrity."""

from __future__ import annotations

import asyncio
from dataclasses import replace
from unittest.mock import patch

import pytest

from study_agent.domain.document_index import (
    DocumentIndex,
    DocumentNode,
    LocatorKind,
    SourceLocator,
)
from study_agent.flashcards.semantic import generation_unit
from study_agent.ingestion import chunk_text
from study_agent.knowledge import document_index as locators
from study_agent.knowledge import unitizer
from tests.unit.flashcards.test_semantic import Judge, analyzer
from tests.unit.knowledge.test_document_index import context


def fixture() -> tuple[locators.DocumentIndexContext, DocumentIndex]:
    text = "".join(f"Café {n} " + "x" * 190 + "\n" for n in range(700))
    binding = context(text=text)
    root = DocumentNode(
        "root",
        None,
        "Synthetic",
        None,
        SourceLocator(LocatorKind.MARKDOWN_LINE_RANGE, start_line=1, end_line=700),
        0,
        tuple(f"n{n}" for n in range(700)),
    )
    nodes = (
        root,
        *tuple(
            DocumentNode(
                f"n{n}",
                "root",
                f"Section {n}",
                None,
                SourceLocator(LocatorKind.MARKDOWN_LINE_RANGE, start_line=n + 1, end_line=n + 1),
                n + 1,
            )
            for n in range(700)
        ),
    )
    return binding, DocumentIndex(
        binding.source.source_id,
        binding.source.revision_id,
        binding.substrate.substrate_id,
        "1",
        "synthetic",
        "1",
        "config",
        nodes,
    )


def test_line_map_and_node_spans_are_bounded_and_resolved_once() -> None:
    locators._text_binding.cache_clear()
    locators._verified_node_spans.cache_clear()
    binding, index = fixture()
    with patch.object(locators, "resolve_locator", wraps=locators.resolve_locator) as resolve:
        first = locators.verified_node_spans(index, binding)
        assert len(first) == resolve.call_count == 701
        assert locators.verified_node_spans(index, binding) == first
        assert resolve.call_count == 701
        # Returned dictionaries cannot modify the cached preparation.
        first.clear()
        assert len(locators.verified_node_spans(index, binding)) == 701
    assert locators._text_binding.cache_info().misses == 1
    assert locators._text_binding.cache_info().maxsize == 4
    assert locators._verified_node_spans.cache_info().maxsize == 4
    spans = locators.verified_node_spans(index, binding)
    assert spans["n0"].start_offset == 0
    assert spans["n699"].end_offset == len(binding.text)
    assert binding.text[spans["n1"].start_offset :].startswith("Café 1 ")


def test_selected_projection_equals_global_and_verifies_unselected_chunks() -> None:
    binding, index = fixture()
    chunks = chunk_text(
        binding.text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )
    global_candidates = unitizer.candidates_for_canonical_chunks(index, binding, chunks)
    selected = (chunks[2], chunks[5])
    projected = unitizer.candidates_for_canonical_chunks(
        index,
        binding,
        chunks,
        selected_chunks=selected,
    )
    assert projected == (global_candidates[2], global_candidates[5])
    damaged = (*chunks[:-1], replace(chunks[-1], checksum_sha256="0" * 64))
    with pytest.raises(ValueError, match="digest or identity"):
        unitizer.candidates_for_canonical_chunks(index, binding, damaged, selected_chunks=selected)
    with pytest.raises(ValueError, match="not canonical"):
        unitizer.candidates_for_canonical_chunks(
            index,
            binding,
            chunks,
            selected_chunks=(replace(chunks[2], metadata={"tampered": True}),),
        )
    invalid_root = replace(
        index.nodes[0],
        locator=SourceLocator(
            LocatorKind.MARKDOWN_LINE_RANGE,
            start_line=2,
            end_line=700,
        ),
    )
    invalid_index = replace(index, nodes=(invalid_root, *index.nodes[1:]), fingerprint="")
    with pytest.raises(ValueError, match="complete substrate"):
        unitizer.candidates_for_canonical_chunks(invalid_index, binding, chunks, selected_chunks=())


def test_semantic_cache_reuses_only_selected_preparation_and_detects_tampering() -> None:
    binding, index = fixture()
    chunks = chunk_text(
        binding.text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )
    from study_agent.flashcards import semantic
    from study_agent.flashcards.planning import CanonicalSourceSpan

    selected = chunks[2]
    scope = (
        CanonicalSourceSpan(
            selected.source_id,
            selected.revision_id,
            selected.start_offset,
            selected.end_offset,
            "test",
        ),
    )
    service = analyzer(Judge())
    with (
        patch.object(
            semantic,
            "candidates_for_canonical_chunks",
            wraps=unitizer.candidates_for_canonical_chunks,
        ) as project,
        patch.object(
            unitizer, "_validate_canonical_chunks", wraps=unitizer._validate_canonical_chunks
        ) as verify,
    ):
        key = service.cache_key_for("lesson", index, binding, canonical_chunks=chunks, scope=scope)
        analysis = asyncio.run(
            service.analyze("lesson", index, binding, canonical_chunks=chunks, scope=scope)
        )
        service.validate_cached(
            analysis, "lesson", index, binding, canonical_chunks=chunks, scope=scope
        )
        generation_unit(analysis, title="Synthetic", context=binding)
        assert project.call_count == verify.call_count == 1
        assert project.call_args.kwargs["selected_chunks"] == (selected,)
        assert analysis.cache_key == key and len(analysis.candidates) == 1
        damaged = (*chunks[:-1], replace(chunks[-1], checksum_sha256="0" * 64))
        with pytest.raises(ValueError, match="digest or identity"):
            service.cache_key_for("lesson", index, binding, canonical_chunks=damaged, scope=scope)
    # Different normalization binding must not reuse a prior cache identity.
    changed = locators.DocumentIndexContext(
        replace(binding.source, normalization_version="new"),
        replace(binding.substrate, normalization_version="new"),
        binding.text,
    )
    assert (
        service.cache_key_for("lesson", index, changed, canonical_chunks=chunks, scope=scope) != key
    )
    for n in range(6):
        service.cache_key_for(f"lesson-{n}", index, binding, canonical_chunks=chunks, scope=scope)
    assert len(service._preparations) == 4
