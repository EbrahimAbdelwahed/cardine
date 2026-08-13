from __future__ import annotations

from hashlib import sha256
from pathlib import Path

import pytest

from cardine.adapters.pageindex import PageIndexCoordinator, PageIndexRevision
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


def test_duplicate_or_unmappable_nodes_degrade_without_evidence(tmp_path: Path) -> None:
    revision = _revision("# Lezione 1\nA\n")

    class EmptyWorker:
        def run(self, _content: str) -> list[object]:
            return [{"node_id": "0001", "title": "x", "text": "not canonical", "line_num": 1}]

    projection = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"), worker=EmptyWorker()
    ).process(revision)
    assert projection.status is PageIndexStatus.DEGRADED
    assert projection.candidates == ()


def test_rebuild_disable_enable_and_bounded_retry(tmp_path: Path) -> None:
    revision = _revision()

    class FailingWorker:
        def run(self, _content: str) -> list[object]:
            raise RuntimeError("not called")

    coordinator = PageIndexCoordinator(
        SQLiteRunStore(tmp_path / "runs.sqlite3"), worker=FailingWorker(), max_attempts=1
    )
    failed = coordinator.process(revision)
    assert failed.status is PageIndexStatus.FAILED
    assert failed.error_code == "pageindex_worker_protocol"
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
    )
    namespaced = NamespacedSQLiteRunStore(store, "cardine-pageindex")
    assert namespaced.compare_and_set(_key(revision), _encode(queued), _encode(indexing))

    failed = coordinator.process(revision)

    assert failed.status is PageIndexStatus.FAILED
    assert failed.error_code == "pageindex_retry_exhausted"
    assert failed.lease_until is None
