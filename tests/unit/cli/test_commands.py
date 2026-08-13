from __future__ import annotations

import os
import signal

from cardine.cli.commands import _DeferredSigint


def test_automatic_ask_region_defers_injected_sigint_until_atomic_operation_finishes() -> None:
    mutations: list[str] = []
    deferred = _DeferredSigint(enabled=True)
    with deferred:
        mutations.append("session-started")
        os.kill(os.getpid(), signal.SIGINT)
        mutations.extend(("run-created", "answer-committed"))
    assert mutations == ["session-started", "run-created", "answer-committed"]
    assert deferred.pending is True
