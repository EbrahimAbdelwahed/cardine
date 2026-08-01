# Log: TUT-08A durable adaptive conversation

Date: 2026-07-30 15:00
Area: adaptive-tutor / product shell

## Summary

Implemented ADR-0015 with a durable SQLite continuation adapter and one
provider-neutral `ConversationTurnApplication`. Direct messages, learner
questions, suspension, restart, resume, exact retries, stale requests, and
failure outcomes are now coordinated without changing `TutorSnapshotV1`.

Independent tests and semantic review found and fixed cross-session identity,
resolved-resume retry, command-bound presentation, and mismatched
runner/continuation-store composition defects.

## Files Changed

- `src/study_agent/adapters/sqlite/tutor_continuations.py`: bounded, no-follow
  operational continuation persistence.
- `src/study_agent/application/conversation_turn.py`: canonical conversation
  orchestration.
- `src/study_agent/cli/repository.py`: optional explicit conversation
  composition with one shared continuation store.
- `tests/unit/application/test_conversation_turn.py`: command/error contracts.
- `tests/integration/test_conversation_turn_application.py`: restart, retry,
  suspension, resume, race, scoping, and failure coverage.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/application/test_conversation_turn.py tests/integration/test_conversation_turn_application.py tests/integration/test_tutor_host_runner.py tests/unit/sessions tests/unit/cli/test_repository.py`:
  107 passed.
- Focused Ruff: passed.
- `git diff --check`: passed.

## Notes

- Provider selection and browser binding remain TUT-08B.
- A runner must be composed with the exact continuation store passed to the
  application; incomplete/mismatched composition is rejected.
