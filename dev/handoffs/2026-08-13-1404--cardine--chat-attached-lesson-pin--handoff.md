# Handoff: chat-attached lesson pin

Date: 2026-08-13 14:04 CEST
Area: Cardine / Wave A / browser + tutor conversation

## Current State

The home-screen lesson picker is styled and reduced to what it is for: choosing
one lesson. The chosen lesson is an attachment on the chat composer and travels
with the turn, so questions and flashcard requests are typed in the chat and
still resolve inside that one source.

All of it is **uncommitted working-tree state** on
`codex/cardine-wave-a-recovery`, alongside unrelated in-flight work from another
lane. Nothing was staged, committed or pushed by this session.

## Completed

- Lesson picker restyled as a disclosure card; untyped text inputs across the
  shell now pick up the design-system field style.
- Per-lesson question and flashcard forms removed; pinned lesson shown as a
  composer attachment with `Rimuovi`, on the home screen and in the chat.
- `lesson_pin` accepted on `/api/v1/session/turns` and continuation responses,
  validated before provider construction, and given precedence over the lesson
  inferred from the wording of the turn.
- `tests/unit/cli/test_chat_attached_lesson_pin.py` added.
- The dead `tests/unit/demo` gates repointed to `src/cardine/demo`; the demo unit
  lane is green again.

## Remaining

- Run the live journey once with a real credential: pin a lesson at
  `http://127.0.0.1:8765/`, ask in chat, confirm the citations stay inside the
  pinned revision, then commit and push this slice with its memory.
- `/api/v1/lessons/ask` and `/api/v1/lessons/flashcards` are now server-only:
  no browser path calls them. Decide whether they stay as a contract surface or
  are retired.
- Decide whether the attachment should survive a page reload; today it is
  browser-local session state and is dropped.
- Pre-existing and untouched: the red-by-design
  `tests/unit/cli/test_live_lesson_recovery_contract.py`, the
  `test_full_product_closure` provider failure, and the `F841` in
  `src/cardine/knowledge/lesson_selection.py:205`.

## Important Context

- An attached lesson is an explicit learner decision and must outrank any lesson
  named in the text of the turn. Do not reverse that precedence.
- A foreign or stale pin must fail the turn with a 400, never silently widen
  retrieval back to the whole course.
- The new turn payload key is additive and optional; the command shape check
  stays strict about every undeclared key.
- Do not disturb the parallel lesson-routing lane in the same working tree
  (`src/cardine/hosts/source_grounding.py`,
  `dev/plans/2026-08-13-1405--cardine--lesson-routing-usability--plan.md`,
  `tests/integration/demo/TUT08/test_routing_recovery.py`) or the AnyDoc work.

## Verification

- `python -m pytest tests/unit/cli tests/unit/demo --ignore=tests/unit/cli/test_live_lesson_recovery_contract.py`: 170 passed.
- `python -m pytest tests/integration/demo/TUT08`: 66 passed, 1 pre-existing failure.
- `python -m ruff check src/cardine/demo src/cardine/cli tests/unit/cli/test_chat_attached_lesson_pin.py`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- Live logged-in journey: not run, no password available in this session.
