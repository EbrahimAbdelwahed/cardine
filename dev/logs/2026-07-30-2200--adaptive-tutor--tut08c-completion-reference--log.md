# Log: TUT-08C C1 completion reference and grounded explain recovery

Date: 2026-07-30 22:00
Area: adaptive tutor / Cardine product shell

## Summary

Implemented the first TUT-08C vertical. The host runner now emits a closed
capability completion reference after gateway verification. A private
completion-handler registry recovers the trusted explain owner output without
copying generic host output into conversation state. Recovered grounded
segments are rendered with bounded source/revision/chunk metadata and persisted
through the existing tutor-presentation owner. Unknown, tampered, or
unrecoverable references remain status-only; insufficient-evidence termination
returns a truthful terminal status without fabricated tutor text.

## Files Changed

- `src/study_agent/hosts/contracts.py`: closed completion reference.
- `src/study_agent/hosts/runner.py`: completion-reference mapping and output binding.
- `src/study_agent/application/capability_completion.py`: private handler registry and typed receipt.
- `src/study_agent/application/conversation_turn.py`: completion recovery and canonical presentation commit.
- `src/study_agent/cli/repository.py`: verified explain owner recovery and bounded citation rendering.
- `src/study_agent/demo/ui_application.py`: selected-session tutor composition.
- `tests/unit/application/test_capability_completion.py`: registry/tamper/status-only coverage.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: grounded completion reload/recovery coverage.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/application/test_capability_completion.py tests/unit/hosts/test_tutor_host_contracts.py tests/integration/test_tutor_host_runner.py tests/unit/application/test_conversation_turn.py tests/integration/test_conversation_turn_application.py tests/unit/cli/test_repository.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: 106 passed, 2 skipped (sandbox loopback restriction).
- `python -m ruff check ...`: passed.
- `git diff --check`: passed.

## Notes

- The full generated-artifact runtime remains a separate TUT-08C follow-up;
  this slice does not invent a lesson/exam owner.

## P2 Composition Audit

The repository composition root now injects the canonical completion-handoff
store into both the production runner and conversation application.  When an
injected runner exposes a handoff store, repository and application
composition fail fast unless the exact same object is supplied; this prevents
a runner from completing against one durable slot while the application reads
another.  Legacy direct-message test doubles that do not expose the optional
completion store remain compatible.

Verification: the focused C1 matrix passed with 129 tests and 2 loopback
sandbox skips; Ruff and `git diff --check` passed.
