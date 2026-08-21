# Log: chat-attached lesson pin

Date: 2026-08-13 14:04 CEST
Area: Cardine / Wave A / browser + tutor conversation

## Summary

Completed in two steps, both on the working tree of
`codex/cardine-wave-a-recovery` (not committed by this session).

**Step 1 — home-screen lesson picker (UI only).** The `Cerca una lezione` block
had no CSS at all: label, input and button fell into default browser layout
directly under the composer, and the search `<input>` carried no `type`, so the
design-system field rule (`input[type="text"], …`) never matched it and the
browser's native box was used. The block is now a quiet `<details>` under the
composer (`Studia una lezione specifica`) that opens by itself when a search or
a pin exists, laid out as a card with the same surface, radius and spacing
tokens as the rest of the shell. Results are rows with title plus
`source_id · revision_id`, the selected row carries a ring, and an empty search
says so instead of rendering nothing.

On the author's instruction the per-lesson `Domanda sulla lezione selezionata`
and `Richiesta flashcard` forms were removed: those belong in the chat. The
pinned lesson is now presented as an attachment strip sitting on top of the
composer (`FONTE ALLEGATA · titolo · revisione`, with `Rimuovi`), on the home
screen and above the chat composer.

**Step 2 — the attachment is now a real turn input.** Removing the two forms
made `/api/v1/lessons/ask` and `/api/v1/lessons/flashcards` unreachable from the
browser, so the pin had to reach the tutor turn itself. It now travels with
`/api/v1/session/turns` and with continuation responses, and an attached lesson
outranks a lesson reference inferred from the wording of the turn
(`resolve_lesson_scope`). The existing `_StructuralRangeRetrieval` scoping and
its `retrieval_limit = 100` are reused unchanged, so a chat answer with a lesson
attached retrieves exactly what the removed `lessons/ask` path retrieved.

Plan: [chat-attached lesson pin](../plans/2026-08-13-1352--cardine--chat-attached-lesson-pin--plan.md).

## Files Changed

- `src/cardine/demo/browser.css`: lesson-study disclosure/card, result rows,
  composer attachment strip, narrow-width stacking; `input:not([type])` added to
  the field rule so untyped text inputs stop falling back to the native box.
- `src/cardine/demo/browser.js`: `renderLessonStudy` restructured; new
  `lessonPin`, `lessonPinAttachment`, `unpinLesson`; attachment rendered above
  both composers; `submitTurn` sends `lesson_pin`; removed `askPinnedLesson`,
  `createPinnedFlashcards` and the removed forms' bindings and `lessonAnswer`
  renderer.
- `src/cardine/demo/ui_application.py`: turn payload accepts an optional
  `lesson_pin`, decoded with the existing `_lesson_pin_payload_from_json`;
  `_command` grew `optional_payload_keys` while staying strict about anything
  undeclared; a foreign or stale attachment answers 400 instead of the generic
  503 runtime path.
- `src/cardine/cli/repository.py`: `tutor_conversation(..., lesson_pin=...)`
  validates course ownership and the pin before any provider construction and
  forwards it to `_RepositoryTutorGateway`, where an explicit pin outranks
  `resolve_lesson_scope`.
- `tests/unit/cli/test_chat_attached_lesson_pin.py`: new.
- `tests/unit/demo/test_design_system.py`, `test_browser_assets.py`,
  `test_ai_primitives.py`, `test_private_access.py`: repointed from the
  pre-rename `src/study_agent/demo` path to `src/cardine/demo`. See
  [the note](../notes/2026-08-13-1404--tests--demo-asset-gates-pointed-at-pre-rename-path--note.md).

## Verification

- `python -m pytest tests/unit/cli/test_chat_attached_lesson_pin.py`: 5 passed.
- `python -m pytest tests/unit/cli tests/unit/demo --ignore=tests/unit/cli/test_live_lesson_recovery_contract.py`:
  170 passed. Before the demo-path repair the same selection was 19 failed /
  138 passed; a stash-and-compare confirmed those 19 were all pre-existing and
  none were caused by this change.
- `python -m pytest tests/integration/demo/TUT08`: 66 passed, 1 failed
  (`test_full_product_closure.py::test_repository_route_control_matrix_and_restart_safe_chat`,
  "model provider is unavailable"). The same test fails identically with this
  change stashed, so it is pre-existing.
- `python -m ruff check src/cardine/demo src/cardine/cli tests/unit/cli/test_chat_attached_lesson_pin.py`: passed.
  `ruff check src/cardine` also reports a pre-existing `F841` unused `needle` in
  `src/cardine/knowledge/lesson_selection.py:205`, untouched here.
- `node --check src/cardine/demo/browser.js`: passed.
- Visual check of the new layout in light and dark against the live
  stylesheet served by the running server: the attachment strip merges with the
  composer, the search row aligns, and the selected-result ring is visible in
  both themes.

## Notes

- The live end-to-end journey was **not** run: `http://127.0.0.1:8765/` is in
  private mode and this session had no password, so the logged-in home screen
  could not be exercised. The visual check used the live `browser.css` against a
  static harness page reproducing the rendered markup. A human should still pin
  a lesson on the live server, ask in chat, and confirm the answer cites only
  the pinned revision.
- `tests/unit/cli/test_live_lesson_recovery_contract.py` (untracked) still fails
  as designed; it documents the unfixed live lesson-turn defect and was failing
  identically before this change.
- The working tree also carries unrelated in-flight work from another lane
  (`src/cardine/hosts/source_grounding.py`, the AnyDoc modifications,
  `dev/plans/2026-08-13-1405--cardine--lesson-routing-usability--plan.md`,
  `tests/integration/demo/TUT08/test_routing_recovery.py`). None of it was
  touched or committed here.
- Follow-up worth considering: the attachment is browser-local session state, so
  a page reload drops it. If it should survive a reload it needs to become
  session state on the server, which is a separate contract decision.
