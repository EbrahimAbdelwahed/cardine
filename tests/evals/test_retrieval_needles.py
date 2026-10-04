from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from cardine.evaluation.retrieval import GoldSpan, NeedleCase, SearchPlan, measure_case
from study_agent.adapters.sqlite import SQLiteFtsRetrieval
from study_agent.domain import ChunkId, Citation, ResolvedCitation
from study_agent.ports.retrieval import RetrievalDocument, RetrievalQuery
from tests.evals.test_lexical_retrieval_fixtures import fixture_document


class Catalog:
    def __init__(self, texts: tuple[str, ...]) -> None:
        self.items = tuple(
            replace(
                fixture_document(f"chunk-{index}", "enzyme", text),
                chunk=replace(
                    fixture_document(f"chunk-{index}", "enzyme", text).chunk,
                    start_offset=sum(len(value) for value in texts[:index]),
                    end_offset=sum(len(value) for value in texts[:index + 1]),
                    ordinal=index,
                    section_path=("Enzyme",),
                ),
            ) for index, text in enumerate(texts)
        )

    def documents(self, *, include_superseded: bool = False) -> tuple[RetrievalDocument, ...]:
        return tuple(item for item in self.items if include_superseded or item.is_current_revision)

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument:
        return next(item for item in self.items if item.chunk.chunk_id == chunk_id)

    def resolve(self, citation: Citation) -> ResolvedCitation:
        document = self.canonical_document(citation.chunk_id)
        assert citation.source_id == document.source_id
        assert citation.revision_id == document.revision_id
        assert document.chunk.start_offset <= citation.start_offset < citation.end_offset
        assert citation.end_offset <= document.chunk.end_offset
        text = document.text[
            citation.start_offset - document.chunk.start_offset:
            citation.end_offset - document.chunk.start_offset
        ]
        return ResolvedCitation(replace(citation, quoted_snippet=text), text)


def setup_needle(tmp_path: Path) -> tuple[Catalog, SQLiteFtsRetrieval, NeedleCase]:
    # Independent synthetic material models a definition followed by a
    # mechanism paragraph that does not repeat the entity's complete name.
    catalog = Catalog((
        "A catalytic triad belongs to enzyme zeta.",
        "Histidine removes a proton and activates the nucleophile.",
        "The activated nucleophile attacks the substrate carbonyl.",
    ))
    retrieval = SQLiteFtsRetrieval(tmp_path / "needles.sqlite3", catalog)
    retrieval.index(catalog.items)
    spans = tuple(GoldSpan(
        item.source_id, item.revision_id, item.chunk.start_offset, item.chunk.end_offset,
        sha256(item.text.encode()).hexdigest(),
    ) for item in catalog.items)
    case = NeedleCase(
        "mechanism", RetrievalQuery(catalog.items[0].course_id, "catalytic triad zeta"),
        {"definition": (spans[0],), "mechanism": spans[1:]},
        alternatives=("histidine proton", "nucleophile carbonyl"),
    )
    return catalog, retrieval, case


def test_nonempty_definition_is_a_mechanism_miss(tmp_path: Path) -> None:
    catalog, retrieval, case = setup_needle(tmp_path)
    result = measure_case(case, SearchPlan("baseline"), retrieval, catalog)
    assert result["search_statuses"] == ["sufficient"]
    assert result["facets"] == {"definition": True, "mechanism": False}
    assert result["complete"] is False
    assert result["false_sufficient"] is True


def test_climbing_and_neighbors_recover_exact_missing_facets(tmp_path: Path) -> None:
    catalog, retrieval, case = setup_needle(tmp_path)
    for plan in (SearchPlan("climb", climb=True), SearchPlan("neighbors", neighbor_radius=2)):
        result = measure_case(case, plan, retrieval, catalog)
        assert result["complete"] is True
        assert result["context_chunks"] == 3
        assert all(item.text not in str(result) for item in catalog.items)


def test_context_budget_cannot_count_unreturned_gold(tmp_path: Path) -> None:
    catalog, retrieval, case = setup_needle(tmp_path)
    result = measure_case(
        case, SearchPlan("limited", climb=True, neighbor_radius=2, context_limit=1),
        retrieval, catalog,
    )
    assert result["context_chunks"] == 1
    assert result["complete"] is False


def test_gold_rejects_stale_hash_and_wrong_query_scope(tmp_path: Path) -> None:
    catalog, retrieval, case = setup_needle(tmp_path)
    wrong = replace(case.facets["definition"][0], checksum="0" * 64)
    with pytest.raises(ValueError, match="checksum"):
        measure_case(replace(case, facets={"definition": (wrong,)}), SearchPlan("base"),
                     retrieval, catalog)
    with pytest.raises(ValueError, match="authorized scope"):
        measure_case(replace(case, query=replace(case.query, source_roles=("other",))),
                     SearchPlan("base"), retrieval, catalog)


def test_neighbor_expansion_respects_section_and_trust(tmp_path: Path) -> None:
    catalog, _, case = setup_needle(tmp_path)
    catalog.items = (
        catalog.items[0],
        replace(catalog.items[1], trust_level=0),
        replace(catalog.items[2], chunk=replace(catalog.items[2].chunk, section_path=("Other",))),
    )
    retrieval = SQLiteFtsRetrieval(tmp_path / "filtered.sqlite3", catalog)
    retrieval.index(catalog.items)
    # Only the seed is gold in this case; neighbors must obey search policy.
    case = replace(case, facets={"definition": case.facets["definition"]},
                   query=replace(case.query, minimum_trust_level=80))
    result = measure_case(case, SearchPlan("neighbors", neighbor_radius=2), retrieval, catalog)
    assert result["context_chunks"] == 1


def test_explicit_negative_counts_spurious_hits(tmp_path: Path) -> None:
    catalog, retrieval, case = setup_needle(tmp_path)
    negative = replace(case, facets={}, expected_empty=True,
                       query=replace(case.query, text="zeta nonexistent-residue"))
    assert measure_case(negative, SearchPlan("base"), retrieval, catalog)["complete"] is True
    spurious = replace(negative, query=replace(case.query, text="zeta triad nonexistent-residue"))
    result = measure_case(spurious, SearchPlan("base"), retrieval, catalog)
    assert result["complete"] is False
    assert result["false_sufficient"] is True


def test_gold_can_cross_adjacent_chunks_without_rank_double_count(tmp_path: Path) -> None:
    catalog, retrieval, case = setup_needle(tmp_path)
    first, second = catalog.items[:2]
    span = GoldSpan(
        first.source_id, first.revision_id, first.chunk.start_offset,
        second.chunk.end_offset, sha256((first.text + second.text).encode()).hexdigest(),
    )
    result = measure_case(replace(case, facets={"joined": (span,)}),
                          SearchPlan("climb", climb=True), retrieval, catalog)
    assert result["complete"] is True
    assert result["context_chunks"] == 3
