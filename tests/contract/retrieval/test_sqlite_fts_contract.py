from __future__ import annotations

import sqlite3
from dataclasses import replace
from hashlib import sha256
from pathlib import Path

import pytest

from study_agent.adapters.sqlite import (
    RetrievalIndexIntegrityError,
    SQLiteFtsRetrieval,
    compile_literal_query,
    normalize_bm25_score,
)
from study_agent.adapters.sqlite.fts_retrieval import (
    RETRIEVAL_STRATEGY_ID,
    RETRIEVAL_STRATEGY_VERSION,
)
from study_agent.domain import (
    ChunkId,
    Citation,
    CourseId,
    ResolvedCitation,
    RevisionId,
    SourceChunk,
    SourceId,
    SourceKind,
)
from study_agent.ports.retrieval import (
    EvidenceStatus,
    IndexReceipt,
    RetrievalDocument,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    RetrievalQuery,
    retrieval_catalog_fingerprint,
    retrieval_read_set_fingerprint,
)


class CanonicalContent:
    def __init__(self) -> None:
        self._documents: dict[ChunkId, RetrievalDocument] = {}

    def add(self, document: RetrievalDocument) -> None:
        if document.is_current_revision:
            for chunk_id, existing in tuple(self._documents.items()):
                if (
                    existing.course_id == document.course_id
                    and existing.source_id == document.source_id
                    and existing.revision_id != document.revision_id
                ):
                    self._documents[chunk_id] = replace(
                        existing, is_current_revision=False
                    )
        self._documents[document.chunk.chunk_id] = document

    def documents(
        self, *, include_superseded: bool = False
    ) -> tuple[RetrievalDocument, ...]:
        return tuple(
            item
            for item in self._documents.values()
            if include_superseded or item.is_current_revision
        )

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument:
        return self._documents[chunk_id]

    def get_text(self, revision_id: RevisionId) -> str:
        return next(
            document.text
            for document in self._documents.values()
            if document.revision_id == revision_id
        )

    def resolve(self, citation: Citation) -> ResolvedCitation:
        document = self._documents[citation.chunk_id]
        if (
            citation.source_id != document.source_id
            or citation.revision_id != document.revision_id
            or citation.start_offset != document.chunk.start_offset
            or citation.end_offset != document.chunk.end_offset
        ):
            raise ValueError("citation mismatch")
        if citation.quoted_snippet is not None and citation.quoted_snippet != document.text:
            raise ValueError("quote mismatch")
        canonical = Citation(
            citation.source_id,
            citation.revision_id,
            citation.chunk_id,
            citation.start_offset,
            citation.end_offset,
            f"canonical:{citation.chunk_id}",
            document.text,
        )
        return ResolvedCitation(canonical, document.text)


def document(
    chunk_name: str,
    text: str,
    *,
    course: str = "course-1",
    source: str = "source-1",
    revision: str = "revision-1",
    kind: SourceKind = SourceKind.TEXT,
    role: str = "primary",
    trust: int = 80,
    current: bool = True,
) -> RetrievalDocument:
    digest = sha256(text.encode()).hexdigest()
    source_id = SourceId(source)
    revision_id = RevisionId(revision)
    chunk = SourceChunk(
        ChunkId(chunk_name),
        source_id,
        revision_id,
        0,
        len(text),
        (),
        0,
        digest,
        "chunker-v1",
    )
    return RetrievalDocument(
        CourseId(course),
        source_id,
        revision_id,
        chunk,
        text,
        "Fixture",
        kind,
        role,
        trust,
        current,
    )


def adapter(tmp_path: Path, documents: tuple[RetrievalDocument, ...]) -> SQLiteFtsRetrieval:
    content = CanonicalContent()
    for item in documents:
        content.add(item)
    result = SQLiteFtsRetrieval(tmp_path / "retrieval.sqlite3", content)
    result.index(documents)
    return result


def test_literal_query_compilation_never_preserves_fts_control_syntax() -> None:
    assert compile_literal_query('heart OR "lung" -excluded column:value') == (
        '"heart" AND "or" AND "lung" AND "excluded" AND "column" AND "value"'
    )
    assert compile_literal_query("***") is None


def test_literal_query_uses_exact_unicode61_tokenization(tmp_path: Path) -> None:
    sharp_s = document("chunk-sharp-s", "Straße café—Herzklappe")
    synthetic_ss = document(
        "chunk-synthetic-ss", "synthetic strasse token", source="source-2"
    )
    retrieval = adapter(tmp_path, (sharp_s, synthetic_ss))

    assert compile_literal_query("Straße café—Herzklappe!!!") == (
        '"straße" AND "cafe" AND "herzklappe"'
    )
    assert compile_literal_query("strasse") == '"strasse"'
    assert [
        item.chunk.chunk_id
        for item in retrieval.search(RetrievalQuery(CourseId("course-1"), "Straße")).evidence
    ] == [ChunkId("chunk-sharp-s")]
    assert [
        item.chunk.chunk_id
        for item in retrieval.search(RetrievalQuery(CourseId("course-1"), "strasse")).evidence
    ] == [ChunkId("chunk-synthetic-ss")]
    assert [
        item.chunk.chunk_id
        for item in retrieval.search(
            RetrievalQuery(CourseId("course-1"), "cafe Herzklappe")
        ).evidence
    ] == [ChunkId("chunk-sharp-s")]


def test_empty_and_matched_results_use_only_insufficient_or_sufficient(tmp_path: Path) -> None:
    retrieval = adapter(tmp_path, (document("chunk-1", "cardiac valve anatomy"),))

    missing = retrieval.search(RetrievalQuery(CourseId("course-1"), "kidney"))
    matched = retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))

    assert missing.status is EvidenceStatus.INSUFFICIENT
    assert missing.evidence == ()
    assert matched.status is EvidenceStatus.SUFFICIENT
    assert matched.evidence[0].text == "cardiac valve anatomy"
    assert matched.evidence[0].citation.quoted_snippet == "cardiac valve anatomy"
    assert 0 <= matched.evidence[0].score <= 1
    assert matched.strategy_id == RETRIEVAL_STRATEGY_ID
    assert matched.strategy_version == RETRIEVAL_STRATEGY_VERSION
    assert len(matched.index_version) == 64
    assert len(matched.read_set_fingerprint) == 64
    assert missing.index_version == matched.index_version
    assert missing.read_set_fingerprint != matched.read_set_fingerprint


def test_retrieval_query_bounds_text_and_limit_before_adapter_work() -> None:
    with pytest.raises(ValueError, match="query limit"):
        RetrievalQuery(CourseId("course-1"), "x" * 513)
    with pytest.raises(ValueError, match="between 1 and 100"):
        RetrievalQuery(CourseId("course-1"), "valve", limit=101)


def test_index_and_search_share_the_exact_content_version(tmp_path: Path) -> None:
    item = document("chunk-versioned", "cardiac valve")
    content = CanonicalContent()
    content.add(item)
    retrieval = SQLiteFtsRetrieval(tmp_path / "versioned.sqlite3", content)

    receipt = retrieval.index((item,))
    result = retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))

    assert receipt.index_version == result.index_version
    assert receipt.catalog_fingerprint == retrieval_catalog_fingerprint((item,))
    assert len(receipt.index_version) == 64


def test_catalog_fingerprint_commits_to_exact_documents_not_only_count() -> None:
    item = document("chunk-versioned", "cardiac valve")
    changed = replace(item, title="Different canonical title")

    assert retrieval_catalog_fingerprint((item,)) != retrieval_catalog_fingerprint((changed,))


def test_all_metadata_filters_apply_before_limit(tmp_path: Path) -> None:
    excluded = document(
        "chunk-a",
        "valve valve valve",
        course="course-other",
        revision="revision-old",
        kind=SourceKind.MARKDOWN,
        role="supplement",
        trust=10,
        current=False,
    )
    allowed = document(
        "chunk-b",
        "valve anatomy",
        revision="revision-new",
        kind=SourceKind.TEXT,
        role="primary",
        trust=90,
    )
    retrieval = adapter(tmp_path, (excluded, allowed))
    query = RetrievalQuery(
        CourseId("course-1"),
        "valve",
        limit=1,
        revision_ids=(RevisionId("revision-new"),),
        source_kinds=(SourceKind.TEXT,),
        source_roles=("primary",),
        minimum_trust_level=80,
    )

    result = retrieval.search(query)

    assert [item.chunk.chunk_id for item in result.evidence] == [ChunkId("chunk-b")]


def test_new_current_revision_supersedes_old_without_deleting_it(tmp_path: Path) -> None:
    old = document("chunk-old", "shared valve", revision="revision-old")
    new = document("chunk-new", "shared valve", revision="revision-new")
    content = CanonicalContent()
    content.add(old)
    retrieval = SQLiteFtsRetrieval(tmp_path / "retrieval.sqlite3", content)
    retrieval.index((old,))
    content.add(new)
    retrieval.index((new,))

    current = retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))
    historical = retrieval.search(
        RetrievalQuery(CourseId("course-1"), "valve", include_superseded=True)
    )

    assert [item.chunk.chunk_id for item in current.evidence] == [ChunkId("chunk-new")]
    assert {item.chunk.chunk_id for item in historical.evidence} == {
        ChunkId("chunk-old"),
        ChunkId("chunk-new"),
    }


def test_tampered_index_text_fails_explicitly(tmp_path: Path) -> None:
    item = document("chunk-1", "canonical valve")
    retrieval = adapter(tmp_path, (item,))
    with sqlite3.connect(tmp_path / "retrieval.sqlite3") as connection:
        connection.execute(
            "UPDATE retrieval_fts SET text = 'tampered valve' WHERE chunk_id = 'chunk-1'"
        )
        connection.commit()

    with pytest.raises(RetrievalIndexIntegrityError, match="derived FTS"):
        retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("course_id", "forged-course"),
        ("source_kind", "markdown"),
        ("source_role", "forged-role"),
        ("trust_level", 0),
        ("is_current_revision", 0),
    ],
)
def test_tampered_filter_metadata_is_detected_before_filtering(
    tmp_path: Path, column: str, value: object
) -> None:
    item = document("chunk-1", "canonical valve")
    retrieval = adapter(tmp_path, (item,))
    with sqlite3.connect(tmp_path / "retrieval.sqlite3") as connection:
        connection.execute(
            f"UPDATE retrieval_documents SET {column} = ? WHERE chunk_id = 'chunk-1'",
            (value,),
        )
        connection.commit()

    with pytest.raises(RetrievalIndexIntegrityError, match="metadata"):
        retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))


def test_partial_revision_and_duplicate_batches_are_rejected_before_writes(
    tmp_path: Path,
) -> None:
    first = document("chunk-a", "valve alpha")
    second = document("chunk-b", "valve beta")
    content = CanonicalContent()
    content.add(first)
    content.add(second)
    retrieval = SQLiteFtsRetrieval(tmp_path / "retrieval.sqlite3", content)

    with pytest.raises(ValueError, match="complete canonical revision"):
        retrieval.index((first,))
    with pytest.raises(ValueError, match="duplicate chunk"):
        retrieval.index((first, first))

    with sqlite3.connect(tmp_path / "retrieval.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM retrieval_fts").fetchone() == (0,)


def test_repeated_complete_batches_are_idempotent_and_ties_are_deterministic(
    tmp_path: Path,
) -> None:
    stronger = document("chunk-a", "valve anatomy")
    weaker = document("chunk-b", "valve anatomy")
    content = CanonicalContent()
    content.add(stronger)
    content.add(weaker)
    retrieval = SQLiteFtsRetrieval(tmp_path / "retrieval.sqlite3", content)
    retrieval.index((stronger, weaker))
    retrieval.index((weaker, stronger))

    result = retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))

    assert [item.chunk.chunk_id for item in result.evidence] == [
        ChunkId("chunk-a"),
        ChunkId("chunk-b"),
    ]
    assert result.evidence[0].score == result.evidence[1].score
    with sqlite3.connect(tmp_path / "retrieval.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM retrieval_documents").fetchone() == (2,)
        assert connection.execute("SELECT COUNT(*) FROM retrieval_fts").fetchone() == (2,)


def test_portable_score_preserves_bm25_direction() -> None:
    assert normalize_bm25_score(-2.0) > normalize_bm25_score(-1.0)
    assert normalize_bm25_score(0.5) > normalize_bm25_score(2.0)


def test_incomplete_rebuild_preserves_existing_index(tmp_path: Path) -> None:
    first = document("chunk-a", "valve alpha")
    second = document("chunk-b", "valve beta")
    retrieval = adapter(tmp_path, (first, second))
    before = retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))

    with pytest.raises(ValueError, match="complete canonical catalog"):
        retrieval.rebuild((first,))

    assert retrieval.search(RetrievalQuery(CourseId("course-1"), "valve")) == before


def test_failed_rebuild_write_rolls_back_to_existing_index(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = document("chunk-a", "valve alpha")
    second = document("chunk-b", "valve beta")
    retrieval = adapter(tmp_path, (first, second))
    before = retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))
    original_upsert = SQLiteFtsRetrieval._upsert

    def fail_after_delete(
        connection: sqlite3.Connection, item: RetrievalDocument
    ) -> None:
        if item.chunk.chunk_id == ChunkId("chunk-b"):
            raise RuntimeError("simulated rebuild write failure")
        original_upsert(connection, item)

    monkeypatch.setattr(SQLiteFtsRetrieval, "_upsert", staticmethod(fail_after_delete))

    with pytest.raises(RuntimeError, match="simulated rebuild write failure"):
        retrieval.rebuild((first, second))

    assert retrieval.search(RetrievalQuery(CourseId("course-1"), "valve")) == before


def test_catalog_evolution_and_missing_or_extra_rows_are_detected(tmp_path: Path) -> None:
    old = document("chunk-old", "valve old", revision="revision-old")
    content = CanonicalContent()
    content.add(old)
    retrieval = SQLiteFtsRetrieval(tmp_path / "retrieval.sqlite3", content)
    retrieval.index((old,))
    content.add(document("chunk-new", "valve new", revision="revision-new"))

    with pytest.raises(RetrievalIndexIntegrityError, match="metadata"):
        retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))

    content = CanonicalContent()
    content.add(old)
    retrieval = SQLiteFtsRetrieval(tmp_path / "missing.sqlite3", content)
    retrieval.index((old,))
    with sqlite3.connect(tmp_path / "missing.sqlite3") as connection:
        connection.execute("DELETE FROM retrieval_fts WHERE chunk_id = 'chunk-old'")
        connection.execute(
            "INSERT INTO retrieval_fts (chunk_id, text) VALUES ('extra', 'valve')"
        )
        connection.commit()
    with pytest.raises(RetrievalIndexIntegrityError, match="FTS"):
        retrieval.search(RetrievalQuery(CourseId("course-1"), "valve"))


def test_result_and_receipt_status_invariants() -> None:
    empty_receipt = (
        "fixture",
        "1.0.0",
        "index-v1",
        retrieval_read_set_fingerprint(()),
    )
    with pytest.raises(ValueError, match="must be non-empty"):
        RetrievalEvidenceSet(EvidenceStatus.SUFFICIENT, (), "a" * 64, *empty_receipt)
    with pytest.raises(ValueError, match="must be non-empty"):
        RetrievalEvidenceSet(EvidenceStatus.CONFLICTING, (), "a" * 64, *empty_receipt)
    with pytest.raises(ValueError, match="must be empty"):
        matched = document("chunk-1", "valve")
        content = CanonicalContent()
        content.add(matched)
        resolved = content.resolve(
            Citation(
                matched.source_id,
                matched.revision_id,
                matched.chunk.chunk_id,
                0,
                len(matched.text),
                "fixture",
            )
        )
        evidence = RetrievalEvidence(matched.chunk, resolved.citation, resolved.text, 0.5)
        items = (evidence,)
        RetrievalEvidenceSet(
            EvidenceStatus.INSUFFICIENT,
            items,
            "a" * 64,
            "fixture",
            "1.0.0",
            "index-v1",
            retrieval_read_set_fingerprint(items),
        )
    with pytest.raises(ValueError, match="non-negative"):
        IndexReceipt(-1, "index-v1", "a" * 64)
    with pytest.raises(ValueError, match="catalog_fingerprint"):
        IndexReceipt(0, "index-v1", "not-a-fingerprint")
