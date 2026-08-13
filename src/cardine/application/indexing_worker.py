"""Isolated process entry point for discardable derived-index work."""

from __future__ import annotations

import sys
from pathlib import Path

from cardine.application.indexing import IndexingStatus
from cardine.cli.repository import LocalRepository


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if len(arguments) != 1:
        return 2
    root = Path(arguments[0])
    # A source may be admitted while an older target is rebuilding. Keep the
    # single worker alive until the newest durable target reaches a terminal
    # state instead of leaving that newer target queued forever.
    for _attempt in range(32):
        with LocalRepository.open(root) as repository:
            record = repository.reconcile_indexing()
        if record.status not in {IndexingStatus.QUEUED, IndexingStatus.INDEXING}:
            return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
