# Log: atomic HUMAN artifact decisions

Date: 2026-08-12 23:40 CEST
Area: Harness artifacts

## Summary

Ported the reviewed bulk HUMAN decision delta from Harness commit
`7920afb3bf3ec06fc98a839ebfcd8e60e3640264` into the Cardine recovery worktree.
The implementation preserves the existing decision event contract while
deriving deterministic child idempotency identities and returning a restart-
stable receipt. All items are validated before one event-store append.

## Files Changed

- `src/study_agent/artifacts/contracts.py`: bulk request, result, and receipt DTOs.
- `src/study_agent/artifacts/events.py`: manifest/request fingerprints and child identities.
- `src/study_agent/artifacts/service.py`: atomic batch command, retry/restart recovery, and conflict checks.
- `src/study_agent/artifacts/__init__.py`: artifact contract exports.
- `src/study_agent/ports/artifact.py`: typed command-port bulk methods.
- `tests/unit/artifacts/test_lifecycle_service.py`: mixed outcomes, retry, changed-manifest, prevalidation, and duplicate-target coverage.
- `tests/integration/test_artifact_bulk_decisions.py`: SQLite atomic append/replay and invalid-last no-partial-append coverage.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/artifacts/test_lifecycle_service.py tests/integration/test_artifact_bulk_decisions.py tests/integration/test_artifact_repository_replay.py tests/integration/test_recall_service.py`: 24 passed.
- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/artifacts tests/integration/test_artifact_bulk_decisions.py tests/integration/test_artifact_repository_replay.py tests/integration/test_headless_artifact_flow.py`: 163 passed.
- `.venv/bin/ruff check ...`: passed.
- `.venv/bin/mypy src/study_agent/artifacts src/study_agent/ports/artifact.py tests/unit/artifacts/test_lifecycle_service.py tests/integration/test_artifact_bulk_decisions.py`: passed.
- `git diff --check`: passed.

## Notes

- The recovery baseline uses the typed `EventStore` port rather than the older
  Harness compatibility helpers; the port adaptation is mechanical.
- No API facade, Cardine product module, schema/event type, store, pyproject,
  or transition overlay was changed. No commit was created by this worker.
