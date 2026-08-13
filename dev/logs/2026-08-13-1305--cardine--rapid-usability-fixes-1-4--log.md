# Log: Cardine rapid usability fixes 1–4

Date: 2026-08-13 13:05
Area: Cardine retrieval, indexing, tutor routing, flashcards UI

## Summary

Completed the four targeted usability fixes required before resuming daily use:

- explicit lesson references resolve a structural lesson range and ground the tutor with every complete canonical chunk in that range;
- source admission queues durable FTS/PageIndex work and returns an observable indexing state to the browser;
- tutor and flashcard routing now documents and recognizes natural positive intents while rejecting meta or explanatory requests;
- flashcard generation exposes a typed settled activity and the browser shows progress before opening Proposte.

Fix 5 (chat message edit/copy controls) remains intentionally out of scope for this pass.

## Files Changed

- `src/cardine/application/indexing.py` and `indexing_worker.py`: durable indexing state coordinator and isolated local worker using the existing SQLite run store.
- `src/cardine/cli/repository.py`: structural lesson scope retrieval and repository indexing orchestration.
- `src/cardine/demo/ui_application.py`: queued upload responses, non-blocking indexing status, worker startup, and flashcard activity receipts.
- `src/cardine/demo/browser.js`: indexing polling, phase copy, flashcard generation feedback, and navigation to Proposte.
- `src/cardine/hosts/flashcard_routing.py`: natural action-plus-card routing with negative guards.
- `src/cardine/knowledge/lesson_selection.py`: preserve canonical section paths on lesson chunks.
- `src/study_agent/prompts/tutor_decision_v1.py`: versioned capability/tool usage guidance and no-future-promise rule.
- `tests/`: focused regressions for structural lesson grounding, durable indexing, natural flashcard routing, API activity, and browser contracts.

## Verification

- `node --check src/cardine/demo/browser.js`: passed.
- `.venv/bin/ruff check <changed Python paths>`: passed.
- Final fix 1–4 suite including Wave A journey: 48 passed, 8 skipped because local sockets are unavailable, 1 unrelated PDF test deselected.
- `PYTHONPATH=.:src .venv/bin/pytest -q tests/integration/demo/TUT08/test_wave_a_study_journey.py tests/unit/cardine/knowledge/test_lesson_selection.py tests/unit/cardine/adapters/pageindex/test_coordinator.py tests/integration/test_fts_retrieval.py`: 9 passed.
- Focused FTS snapshot/lifetime regressions: 2 passed.
- Focused source-grounding, explanation-prompt, and tutor-decision regressions: 19 passed.
- `.venv/bin/python -m mypy`: blocked before analysis by the checkout's pre-existing duplicate module discovery (`hosts.contracts` and `cardine.hosts.contracts`).

## Notes

- The existing AnyDoc PDF sandbox failure remains unrelated to these fixes.
- Source content is canonical; FTS and PageIndex remain discardable derived indexes.
- Generated flashcards remain proposals and do not enter Ripasso before explicit acceptance and enrollment.
