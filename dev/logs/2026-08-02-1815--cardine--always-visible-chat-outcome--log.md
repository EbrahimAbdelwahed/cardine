# Log: always-visible chat outcome

Date: 2026-08-02 18:15
Area: Cardine tutor chat

## Summary

Cardine now settles every terminal tutor turn with one canonical visible
presentation after the learner turn has been persisted. This covers
`start_capability` termination and failure, provider/runtime failure, budget
exhaustion, malformed presentation-bearing host results, runner exceptions,
and completed capability output that cannot be recovered into a presentation.

The application owns settlement and idempotency while the Cardine repository
composition injects localized safe copy. `IN_PROGRESS` remains retryable and
does not create a terminal journal or fallback presentation. The terminal
journal is schema v2, remains able to decode v1 receipts, stores the resolved
safe copy for deterministic recovery, and revalidates it against the trusted
Cardine policy and presentation bounds before replay.

The analogous change was not applied to Study Agent Harness because that
repository does not contain Cardine's `ConversationTurnApplication`. Its
separate shared stop-reason contract change remains in commit `5b0c1c3`.

## Files Changed

- `src/study_agent/application/conversation_turn.py`: terminal settlement,
  exact-retry recovery, injected copy policy, non-optional result presentation,
  v1/v2 terminal journal codec, retryable `IN_PROGRESS`, and safe replay checks.
- `src/study_agent/cli/repository.py`: Cardine-owned Italian fallback policy and
  composition binding.
- `src/study_agent/hosts/contracts.py`: receipt documentation now includes the
  trusted application settlement boundary.
- `tests/integration/test_conversation_turn_application.py`: terminal outcome,
  runner exception, malformed receipt, restart/idempotency, and in-progress
  retry coverage.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: repository/UI
  coverage for `start_capability` completion recovery failure, invalid tutor
  decisions, provider rejection, and malformed grounded output.

## Verification

- `.venv/bin/python -m pytest -q tests/integration/test_conversation_turn_application.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: 45 passed, 2 skipped because local sockets are unavailable.
- `.venv/bin/ruff check src/study_agent/application/conversation_turn.py src/study_agent/cli/repository.py src/study_agent/hosts/contracts.py tests/integration/test_conversation_turn_application.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: passed.
- `.venv/bin/mypy src/study_agent/application/conversation_turn.py src/study_agent/cli/repository.py src/study_agent/hosts/contracts.py tests/integration/test_conversation_turn_application.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: passed.
- `.venv/bin/python -m pytest -q`: 2135 passed, 28 skipped, 8 failed. Four failures require local socket binding; the other four are the same pre-existing playbook/design-system failures documented before this change.
- `git diff --check`: passed.
- Independent architecture review: approved the Cardine application seam after moving localized copy to composition and preserving retryable `IN_PROGRESS`.
- Independent semantic re-review: no blocking/high findings; the Cardine repository mutation lock covers the full application turn.
- Independent security re-review: no blocker after policy recomputation, exact journal equality, and presentation-bound validation.

## Notes

- Product-level repository mutations are serialized for the duration of the
  learner turn, host execution, and presentation settlement.
- A future generic Harness shell may adopt a similar product invariant through
  its own presentation owner, but no corresponding application contract exists
  there today.
