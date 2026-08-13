# Log: Selected-lesson flashcards

Date: 2026-08-13 00:15 CEST
Area: cardine / Wave A

## Summary

Added the Cardine-owned selected-lesson flashcard route. A complete current
`SourcePin` is validated before provider/model construction, then the existing
profile-dispatched Harness capability receives a read-only content view whose
plan, profile reads, commitments, and evidence contain only whole chunks inside
that pin. The route settles the verified batch through a scoped artifact owner.

The browser and CLI expose explicit lesson-scoped generation. Artifact review
now returns bounded prompt/answer blocks/key points plus safe profile and role
metadata; malformed or oversized content is marked unavailable and the browser
does not render decision/enrollment controls for it. The UI bulk route adapts
the existing atomic HUMAN decision service for 1..24 items and preserves
request identity/retry/conflict semantics.

## Files Changed

- `src/cardine/application/flashcard_proposals.py`: scoped source view and
  pin-bound composition.
- `src/cardine/cli/repository.py`: provider-zero pin validation and scoped
  verified generation settlement.
- `src/cardine/cli/commands.py`, `src/cardine/cli/registry.py`: lesson
  flashcard and atomic artifact decision commands.
- `src/cardine/demo/ui_application.py`, `src/cardine/demo/browser.js`:
  shared lesson route, review DTO, browser content gating, and bulk decisions.
- `src/cardine/application/artifact_decisions.py`: one transport-neutral parser,
  atomic command owner, and receipt DTO shared by CLI and browser UI.
- `src/study_agent/ports/verified_batch.py`,
  `src/study_agent/artifacts/verified_batch.py`, and
  `src/study_agent/flashcards/lesson_worker_service.py`: preserve and verify the
  exact profile-bound execution inputs during verified child recovery.
- `src/cardine/application/conversation_turn.py`,
  `src/study_agent/capabilities/morphology_flashcards.py`, and
  `src/study_agent/prompts/morphology_flashcards_v1.py`: preserve the shared
  stream high-water after product settlement and keep the existing morphology
  proposal journey valid.
- `tests/unit/cli/test_selected_lesson_flashcards.py`: scope, foreign/partial
  provider-zero, and review DTO gates.
- `tests/integration/demo/TUT08/test_repository_materials_artifacts_context.py`:
  review-after-restart and UI atomic bulk retry/conflict coverage.

## Verification

- Combined selected-lesson, verified-batch, headless artifact, chat proposal,
  review/recall, browser contract, morphology, and CA-02 audit regressions:
  52 passed, 6 socket-sandbox skips.
- `MYPYPATH=src .venv/bin/python -m mypy --explicit-package-bases` over the ten changed Python source/test paths: passed.
- `PYTHONPATH=src .venv/bin/ruff check ...`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `git diff --check`: passed.

## Notes

- No event schema, storage contract, PageIndex evidence, auto-selection, or
  auto-enrollment behavior was changed.
