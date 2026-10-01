from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from cardine.adapters.document_index.pageindex import PageIndexDocumentIndexAdapter
from study_agent.adapters.filesystem import FilesystemBlobStore
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import CourseId, RevisionId
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.identifiers import substrate_id_for
from study_agent.domain.substrate import Substrate
from study_agent.ingestion import chunk_text, register_source_revision_events
from study_agent.knowledge.document_index import DocumentIndexContext, LocatorReconciliationError
from study_agent.knowledge.unitizer import (
    candidates_for_canonical_chunks,
    draft_units_from_document_index,
    materialize_unit_drafts,
)
from study_agent.ports.document_index import DocumentIndexRequest
from study_agent.retrieval import CourseSourceContent
from study_agent.state import EventRegistry
from tests.integration.test_source_projection_replay import make_event
from tests.unit.knowledge.test_document_index import context, index, node


def test_identity_free_drafts_materialize_to_existing_partial_chunk_citations() -> None:
    binding = context()
    chunks = chunk_text(
        binding.text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )
    assert len(chunks) == 1
    derived = index(
        (node(children=("child",)), node("child", parent="root", order=1, start=5, end=12))
    )
    drafts = draft_units_from_document_index(derived, binding)
    materialized = materialize_unit_drafts(binding, drafts, chunks)
    assert len(materialized) == 3
    assert {citation.chunk_id for unit in materialized for citation in unit.citations} == {
        chunks[0].chunk_id,
    }
    assert [
        (unit.citations[0].start_offset, unit.citations[0].end_offset) for unit in materialized
    ] == [(0, 5), (5, 12), (12, len(binding.text))]
    assert chunks == chunk_text(
        binding.text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )


def test_cross_node_canonical_chunk_is_classified_whole_under_containing_ancestor() -> None:
    binding = context()
    chunks = chunk_text(
        binding.text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )
    derived = index(
        (node(children=("child",)), node("child", parent="root", order=1, start=5, end=12))
    )
    candidates = candidates_for_canonical_chunks(derived, binding, chunks)
    assert len(candidates) == len(chunks) == 1
    assert candidates[0].node_key == "root"
    assert candidates[0].ancestor_keys == ("root",)
    # The root covers exactly this whole canonical chunk, so its summary is local.
    assert candidates[0].summary == "Derived summary"
    assert (candidates[0].span.start_offset, candidates[0].span.end_offset) == (
        chunks[0].start_offset,
        chunks[0].end_offset,
    )


def test_materialization_rejects_missing_text_or_tampered_canonical_chunks() -> None:
    binding = context()
    chunks = chunk_text(
        binding.text,
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        kind=binding.source.kind,
    )
    drafts = draft_units_from_document_index(index(), binding)
    with pytest.raises(LocatorReconciliationError, match="without canonical chunk"):
        materialize_unit_drafts(binding, drafts, ())
    for altered in (
        replace(chunks[0], checksum_sha256="0" * 64),
        replace(chunks[0], revision_id=RevisionId("different")),
        replace(chunks[0], end_offset=chunks[0].end_offset - 1),
    ):
        with pytest.raises(LocatorReconciliationError):
            candidates_for_canonical_chunks(index(), binding, (altered,))
    with pytest.raises(LocatorReconciliationError, match="ordering"):
        materialize_unit_drafts(binding, tuple(reversed(drafts)), chunks + chunks)


def test_whitespace_only_draft_requires_no_fabricated_identity() -> None:
    binding = context(text="\n")
    derived = DocumentIndex(
        binding.source.source_id,
        binding.source.revision_id,
        binding.substrate.substrate_id,
        "1",
        "fixture",
        "1",
        "config",
        (node(end=1),),
    )
    materialized = materialize_unit_drafts(
        binding, draft_units_from_document_index(derived, binding), ()
    )
    assert materialized[0].citations == ()


def test_canonical_citation_resolution_and_replay_survive_structural_materialization(
    tmp_path: Path,
) -> None:
    blobs = FilesystemBlobStore(tmp_path / "blobs")
    registry = EventRegistry()
    register_source_revision_events(registry, blobs.get)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    canonical = make_event(blobs, "Café valves\nHeart muscle".encode(), 1)
    events.append(canonical.course_id, 0, (canonical,))
    catalog = CourseSourceContent(CourseId("course-1"), events, blobs)
    record = catalog.catalog()[0]
    substrate = Substrate(
        substrate_id_for(record.text.encode()),
        record.source.normalized_blob,
        len(record.text),
        record.source.normalization_version,
    )
    binding = DocumentIndexContext(record.source, substrate, record.text)
    request = DocumentIndexRequest(
        record.source.source_id,
        record.source.revision_id,
        substrate.substrate_id,
        "text/plain",
        record.text.encode(),
        record.text,
    )
    derived = PageIndexDocumentIndexAdapter().build_sync(request)
    # A local structural boundary splits a canonical chunk into navigation citations.
    boundary = record.text.index("Heart")
    root = replace(derived.nodes[0], children=("local",))
    local = node("local", parent=root.node_key, order=1, start=boundary, end=len(record.text))
    derived = replace(derived, nodes=(root, local), fingerprint="")
    drafts = draft_units_from_document_index(derived, binding)
    before = events.projection_bytes(canonical.course_id)
    units = materialize_unit_drafts(binding, drafts, record.chunks)
    for unit in units:
        for citation in unit.citations:
            assert (
                catalog.resolve(citation).text
                == record.text[citation.start_offset : citation.end_offset]
            )
    assert events.rebuild_projection(canonical.course_id) == before
    after = catalog.catalog()[0]
    assert after.chunks == record.chunks
    assert materialize_unit_drafts(binding, drafts, after.chunks) == units
    assert events.projection_bytes(canonical.course_id) == before
    blobs.close()
