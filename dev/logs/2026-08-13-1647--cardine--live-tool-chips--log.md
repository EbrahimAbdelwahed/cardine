# Log: live Tool Chips

Date: 2026-08-13 16:47 CEST
Area: Cardine / tutor diagnostics / chat UI

## Summary

Implemented the first, intentionally process-local delivery of truthful Tool Chips in chat. A blocking tutor POST now records bounded safe activities, exposes them through an authenticated polling route that does not acquire the repository mutation lock, and returns the settled records with the tutor receipt. The browser shows those records under the pending and final assistant message.

The legacy session-level “Attività” block was removed; opaque “Stato tutor” diagnostics remain. Reload/restart reconstruction remains deferred to delivery two.

The shared Claude-authored fix for lesson-pinned chat flashcards was preserved and verified in the same worktree.

## Files Changed

- `src/cardine/diagnostics/turn_activity.py`: bounded store, closed vocabulary, privacy validation, context isolation, deduplication, and settlement.
- `src/cardine/demo/ui_application.py`: lock-free activity GET, turn lifecycle capture, receipt records, and exact final-message association.
- `src/cardine/cli/repository.py`: observed retrieval/tool/capability verification and preserved pin-scoped flashcards.
- `src/cardine/adapters/model/tutor_decision.py`: useful capability observation only; harness tools remain observed at actual invocation.
- `src/cardine/demo/ai-primitives.js` and `.css`: pure Tool Chips renderer and scoped visual treatment.
- `src/cardine/demo/browser.js` and `.css`: bounded single-flight polling, receipt correlation by `presentation_id`, and legacy block removal.
- `tests/unit/diagnostics/test_turn_activity.py`, `tests/unit/demo/test_tool_chips*.py`, `tests/unit/demo/test_turn_activity_browser.py`, and `tests/integration/demo/TUT08/test_tool_chips_activity_contract.py`: public behavior and privacy coverage.
- `dev/decisions/2026-08-13--ADR-0001--process-local-turn-activity.md`: transient diagnostics boundary.

## Verification

- Red proof: missing store module, missing renderer/poller, legacy block present, and missing `activity_records` all failed before implementation.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo tests/unit/cli tests/unit/diagnostics` outside the socket sandbox: 186 passed.
- Focused Tool Chips, flashcard pin, design-system, and renderer suite: 59 passed.
- `tests/unit/demo/test_turn_activity_browser.py` outside the socket sandbox: 3 passed, including authenticated lock-independent GET.
- Broad TUT08 run excluding environment limitations: 67 passed, 2 socket skips; two known unrelated failures remain (`test_repository_route_control_matrix_and_restart_safe_chat`, PDF AnyDoc worker).
- Repository browser journey: 7 passed, 1 known failure. The same failing journey was reproduced unchanged on clean base commit `685dce0` (`model.requests == 0`), proving it is not introduced by Tool Chips.
- Node syntax, Ruff, `git diff --check`, one-`innerHTML`, and zero-native-title gates: passed.
- Fresh visual critique after the final revision: no concrete defects; hierarchy, contrast, disclosure affordance, spacing, and scan readability approved.
- Independent semantic re-review after fixes: no unresolved or newly introduced findings.

## Notes

- Activity survives route changes within the current page through a browser-only map keyed by canonical `presentation_id`; reload and server restart intentionally clear it.
- Unknown activity state is retried while the POST remains pending, closing the GET-before-capture race.
- Flashcard tutor turns remain in Chat on settlement so final chips stay under their message; Proposte counts are refreshed and the learner can open Proposte from navigation.
