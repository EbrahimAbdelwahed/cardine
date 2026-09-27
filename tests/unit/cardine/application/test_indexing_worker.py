from __future__ import annotations

import pytest

from cardine.application.indexing import IndexingPhase, IndexingRecord, IndexingStatus
from cardine.application.indexing_worker import main


def test_worker_reconciles_again_when_a_newer_target_is_queued(monkeypatch: pytest.MonkeyPatch) -> None:
    queued = IndexingRecord("a" * 64, IndexingStatus.QUEUED, IndexingPhase.QUEUED)
    ready = IndexingRecord("b" * 64, IndexingStatus.READY, IndexingPhase.COMPLETE, 3)
    records = iter((queued, ready))
    calls: list[str] = []

    class Repository:
        def __enter__(self) -> Repository:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def reconcile_indexing(self) -> IndexingRecord:
            calls.append("reconcile")
            return next(records)

    monkeypatch.setattr(
        "cardine.application.indexing_worker.LocalRepository.open",
        lambda _root: Repository(),
    )

    assert main(["repository"]) == 0
    assert calls == ["reconcile", "reconcile"]
