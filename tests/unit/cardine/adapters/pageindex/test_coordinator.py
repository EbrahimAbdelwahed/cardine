from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from cardine.adapters.pageindex import PageIndexCoordinator, PageIndexRevision, PageIndexWorker
from cardine.adapters.pageindex.coordinator import _encode, _key
from cardine.knowledge import PageIndexProjection, PageIndexStatus
from study_agent.adapters.sqlite import NamespacedSQLiteRunStore, SQLiteRunStore


def _revision(text: str = "# Lezione 1\nContenuto.\n") -> PageIndexRevision:
    return PageIndexRevision(
        "course-1", "source-1", "revision-1", text, sha256(text.encode()).hexdigest()
    )


def test_projection_is_restart_safe_and_maps_canonical_offsets(tmp_path: Path) -> None:
    revision = _revision()
    first = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3"))
    assert first.request(revision).status is PageIndexStatus.QUEUED
    result = first.process(revision)
    assert result.status is PageIndexStatus.READY
    assert result.candidates[0].start_offset == 0
    restarted = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3"))
    assert restarted.load(revision) == result


def test_provider_text_is_ignored_and_navigation_uses_exact_line_locators(tmp_path: Path) -> None:
    revision = _revision("# Lezione 1\nA\n")

    class EmptyWorker(PageIndexWorker):
        def run(self, _content: str) -> list[object]:
            return [{"node_id": "0001", "title": "x", "text": "not canonical", "line_num": 1}]

    projection = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"), worker=EmptyWorker()
    ).process(revision)
    assert projection.status is PageIndexStatus.READY
    assert projection.document_index is not None
    assert projection.candidates[0].start_offset == 0
    assert projection.candidates[0].end_offset == len(revision.content)
    assert b"not canonical" not in _encode(projection)


def test_rebuild_disable_enable_and_bounded_retry(tmp_path: Path) -> None:
    revision = _revision()

    class FailingWorker(PageIndexWorker):
        def run(self, _content: str) -> list[object]:
            raise RuntimeError("not called")

    coordinator = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"), worker=FailingWorker(), max_attempts=1
    )
    failed = coordinator.process(revision)
    assert failed.status is PageIndexStatus.FAILED
    assert failed.error_code == "pageindex_failure"
    disabled = coordinator.disable(revision)
    assert disabled.status is PageIndexStatus.DISABLED
    with pytest.raises(ValueError, match="disabled"):
        coordinator.rebuild(revision)
    enabled = coordinator.enable(revision)
    assert enabled.status is PageIndexStatus.QUEUED


def test_expired_final_attempt_lease_becomes_terminal_failure(tmp_path: Path) -> None:
    revision = _revision()
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    coordinator = PageIndexCoordinator(store, max_attempts=1, clock=lambda: 2.0)
    queued = coordinator.request(revision)
    indexing = PageIndexProjection(
        revision.course_id,
        revision.source_id,
        revision.revision_id,
        revision.content_sha256,
        PageIndexStatus.INDEXING,
        1,
        lease_until=1.0,
        cache_fingerprint=queued.cache_fingerprint,
    )
    namespaced = NamespacedSQLiteRunStore(store, "cardine-pageindex")
    assert namespaced.compare_and_set(_key(revision), _encode(queued), _encode(indexing))

    failed = coordinator.process(revision)

    assert failed.status is PageIndexStatus.FAILED
    assert failed.error_code == "pageindex_retry_exhausted"
    assert failed.lease_until is None


def test_schema2_persists_one_shared_index_and_cache_identity(tmp_path: Path) -> None:
    import json

    from cardine.adapters.pageindex.coordinator import _decode

    revision = _revision()
    coordinator = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3"))
    projection = coordinator.process(revision)
    assert projection.document_index is not None and projection.cache_fingerprint is not None
    assert _decode(_encode(projection)) == projection
    payload = json.loads(_encode(projection))
    assert payload["schema"] == 2
    assert payload["document_index"]["fingerprint"] == projection.document_index.fingerprint


def test_each_status_read_hashes_original_once_and_revalidates_storage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import cardine.adapters.pageindex.coordinator as module

    revision = _revision()
    coordinator = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3"))
    expected = coordinator.process(revision)
    original = revision.content.encode()
    digest = sha256
    hashes = 0

    def counted(data: bytes = b"") -> object:
        nonlocal hashes
        if data == original:
            hashes += 1
        return digest(data)

    monkeypatch.setattr(module, "sha256", counted)
    assert coordinator.load(revision) == expected
    assert hashes == 1, "status hashed the same immutable source twice"
    assert coordinator.load(revision) == expected
    assert hashes == 2, "a later read must still validate its source binding"


def test_schema1_ready_cache_is_readable_but_rebuilt_without_legacy_text_mapping(
    tmp_path: Path,
) -> None:
    import json

    from cardine.adapters.pageindex.coordinator import _decode

    revision = _revision()
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    coordinator = PageIndexCoordinator(store)
    modern = coordinator.process(revision)
    raw = json.loads(_encode(modern))
    raw["schema"] = 1
    del raw["cache_fingerprint"]
    del raw["document_index"]
    raw["candidates"][0]["title"] = "Historical navigation"
    legacy = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    assert _decode(legacy).document_index is None
    storage = NamespacedSQLiteRunStore(store, "cardine-pageindex")
    assert storage.compare_and_set(_key(revision), _encode(modern), legacy)
    assert coordinator.load(revision).document_index is None
    rebuilt = coordinator.process(revision)
    assert rebuilt.document_index is not None and rebuilt.attempt == 1
    assert rebuilt.candidates[0].title != "Historical navigation"
    assert json.loads(storage.load(_key(revision)))["schema"] == 2


def test_worker_configuration_changes_invalidate_ready_index(tmp_path: Path) -> None:
    revision = _revision()
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    first = PageIndexCoordinator(store, worker=PageIndexWorker(timeout_seconds=2))
    initial = first.process(revision)
    second = PageIndexCoordinator(store, worker=PageIndexWorker(timeout_seconds=3))
    stale = second.load(revision)
    assert stale.status is PageIndexStatus.QUEUED
    assert stale.document_index is None and not stale.candidates
    rebuilt = second.process(revision)
    assert initial.document_index is not None and rebuilt.document_index is not None
    assert rebuilt.cache_fingerprint != initial.cache_fingerprint
    assert rebuilt.document_index.config_fingerprint != initial.document_index.config_fingerprint
    assert rebuilt.attempt == 1


def test_disabled_state_survives_configuration_change_until_explicit_enable(tmp_path: Path) -> None:
    revision = _revision()
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    first = PageIndexCoordinator(store, worker=PageIndexWorker(timeout_seconds=2))
    first.process(revision)
    disabled = first.disable(revision)
    assert disabled.document_index is None and not disabled.candidates
    second = PageIndexCoordinator(store, worker=PageIndexWorker(timeout_seconds=3))
    assert second.process(revision) == disabled
    queued = second.enable(revision)
    assert (
        queued.status is PageIndexStatus.QUEUED
        and queued.cache_fingerprint != disabled.cache_fingerprint
    )
    assert second.process(revision).document_index is not None


def test_heading_free_canonical_text_has_explicit_root_navigation(tmp_path: Path) -> None:
    revision = _revision("Canonical text without Markdown headings.\n")
    result = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3")).process(revision)
    assert result.status is PageIndexStatus.READY and result.document_index is not None
    assert len(result.candidates) == 1 and result.candidates[0].node_id == "document-root"
    assert result.candidates[0].end_offset == len(revision.content)


def test_invalid_provider_line_locators_retry_without_legacy_semantic_fallback(
    tmp_path: Path,
) -> None:
    class OutOfBoundsWorker(PageIndexWorker):
        def run(self, _content: str) -> list[object]:
            return [{"node_id": "bad", "title": "Bad", "line_num": 900}]

    coordinator = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"),
        worker=OutOfBoundsWorker(),
        max_attempts=1,
    )
    failed = coordinator.process(_revision())
    assert failed.status is PageIndexStatus.FAILED
    assert failed.error_code == "pageindex_malformed_response"
    assert failed.document_index is None and not failed.candidates


def test_actual_pdf_request_is_indexed_through_same_primary_producer(tmp_path: Path) -> None:
    from study_agent.domain import RevisionId, SourceId, SubstrateId
    from study_agent.ports.document_index import DocumentIndexRequest

    revision = _revision()
    original = b"%PDF-1.7 fixture-original"
    request = DocumentIndexRequest(
        SourceId(revision.source_id),
        RevisionId(revision.revision_id),
        SubstrateId(f"substrate:sha256:{revision.content_sha256}"),
        "application/pdf",
        original,
        revision.content,
        {
            "original_sha256": sha256(original).hexdigest(),
            "page_count": 1,
            "page_map": ({"page": 1, "offset": 0},),
        },
    )
    actual = PageIndexRevision(
        revision.course_id,
        revision.source_id,
        revision.revision_id,
        revision.content,
        revision.content_sha256,
        document_index_request=request,
    )
    coordinator = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3"))
    fallback = coordinator.process(revision)
    pdf = coordinator.process(actual)
    assert pdf.status is PageIndexStatus.READY and pdf.document_index is not None
    assert pdf.cache_fingerprint != fallback.cache_fingerprint
    assert original not in _encode(pdf)


def test_revision_rejects_unbound_index_request() -> None:
    from study_agent.domain import RevisionId, SourceId, SubstrateId
    from study_agent.ports.document_index import DocumentIndexRequest

    revision = _revision()
    request = DocumentIndexRequest(
        SourceId("wrong-source"),
        RevisionId(revision.revision_id),
        SubstrateId(f"substrate:sha256:{revision.content_sha256}"),
        "text/markdown",
        revision.content.encode(),
        revision.content,
    )
    with pytest.raises(ValueError, match="canonical revision"):
        PageIndexRevision(
            revision.course_id,
            revision.source_id,
            revision.revision_id,
            revision.content,
            revision.content_sha256,
            request,
        )


def test_tampered_index_or_navigation_cache_is_rejected(tmp_path: Path) -> None:
    import json

    from cardine.adapters.pageindex.coordinator import _decode

    revision = _revision()
    coordinator = PageIndexCoordinator(SQLiteRunStore(tmp_path / "runs.sqlite3"))
    ready = coordinator.process(revision)
    raw = json.loads(_encode(ready))
    raw["document_index"]["fingerprint"] = "a" * 64
    with pytest.raises(ValueError, match="invalid"):
        _decode(json.dumps(raw, sort_keys=True, separators=(",", ":")).encode())


def test_async_repeated_cancellation_waits_for_bounded_worker_cleanup(tmp_path: Path) -> None:
    import asyncio
    import threading

    entered, release, finished = threading.Event(), threading.Event(), threading.Event()

    class BlockingWorker(PageIndexWorker):
        def run(self, _content: str) -> list[object]:
            entered.set()
            assert release.wait(timeout=2)
            finished.set()
            return []

    coordinator = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"), worker=BlockingWorker()
    )
    revision = _revision()

    async def exercise() -> None:
        task = asyncio.create_task(coordinator.process_async(revision))
        while not entered.is_set():
            await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(exercise())
    assert coordinator.load(revision).status is PageIndexStatus.READY


def test_cache_navigation_cannot_be_substituted_without_matching_index(tmp_path: Path) -> None:
    import json

    revision = _revision()
    store = SQLiteRunStore(tmp_path / "runs.sqlite3")
    coordinator = PageIndexCoordinator(store)
    ready = coordinator.process(revision)
    raw = json.loads(_encode(ready))
    raw["candidates"][0]["start_offset"] = 1
    storage = NamespacedSQLiteRunStore(store, "cardine-pageindex")
    assert storage.compare_and_set(
        _key(revision),
        _encode(ready),
        json.dumps(raw, sort_keys=True, separators=(",", ":")).encode(),
    )
    with pytest.raises(ValueError, match="navigation differs"):
        coordinator.load(revision)


def test_disable_during_worker_prevents_publication_of_completed_index(tmp_path: Path) -> None:
    import asyncio
    import threading

    entered, release = threading.Event(), threading.Event()

    class BlockingWorker(PageIndexWorker):
        def run(self, _content: str) -> list[object]:
            entered.set()
            assert release.wait(timeout=2)
            return []

    coordinator = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"), worker=BlockingWorker()
    )
    revision = _revision()

    async def exercise() -> None:
        task = asyncio.create_task(coordinator.process_async(revision))
        while not entered.is_set():
            await asyncio.sleep(0)
        disabled = coordinator.disable(revision)
        release.set()
        assert await task == disabled

    asyncio.run(exercise())
    disabled = coordinator.load(revision)
    assert disabled.status is PageIndexStatus.DISABLED
    assert disabled.document_index is None and disabled.candidates == ()
