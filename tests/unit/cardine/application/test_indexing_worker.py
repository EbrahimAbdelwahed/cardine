from __future__ import annotations

from cardine.application.indexing import IndexingPhase, IndexingRecord, IndexingStatus
from cardine.application.indexing_worker import main


def test_worker_reconciles_again_when_a_newer_target_is_queued(monkeypatch) -> None:
    queued = IndexingRecord("a" * 64, IndexingStatus.QUEUED, IndexingPhase.QUEUED)
    ready = IndexingRecord("b" * 64, IndexingStatus.READY, IndexingPhase.COMPLETE, 3)
    records = iter((queued, ready))
    calls: list[str] = []

    class Repository:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def reconcile_indexing(self):
            calls.append("reconcile")
            return next(records)

    monkeypatch.setattr(
        "cardine.application.indexing_worker.LocalRepository.open",
        lambda _root: Repository(),
    )

    assert main(["repository"]) == 0
    assert calls == ["reconcile", "reconcile"]
