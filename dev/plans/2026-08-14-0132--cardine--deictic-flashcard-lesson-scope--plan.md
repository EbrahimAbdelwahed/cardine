# Plan: deictic flashcard lesson scope

Date: 2026-08-14 01:32 CEST
Area: Cardine / flashcard generation

## Goal

Make `genera flashcard su questa lezione` reuse the last uniquely resolved
lesson in the same session instead of planning over every course chunk.

## Scope

- In scope:
  - recover one recent explicit lesson reference for a deictic flashcard turn;
  - validate a fresh structural pin and execute the existing pinned generator;
  - correct Italian-language detection for the live wording;
  - one public conversation-turn regression over a source larger than the
    256-entry global planner bound.
- Out of scope:
  - persistent cross-session pins, embeddings, a new job system, planner
    redesign, or generic transcript inference.

## Interface seam

The existing `POST /api/v1/session/turns` application seam: after a successful
explicit lesson turn, a deictic flashcard request must complete and publish
proposals from that lesson only.

## Approach

1. Add the live-shaped regression and prove it fails before generation.
2. Resolve the current query first, then a bounded recent learner lesson
   reference only for deictic lesson wording.
3. Call the unchanged `start_for_pin` path and keep UI-attached pins strongest.

## Risks

- An older unrelated lesson must not be selected for an explicit new scope.
- Ambiguous and stale references must fail closed.
- Concurrent browser/source-viewer edits remain untouched.

## Verification

- Focused public conversation/flashcard regression.
- Existing attached-pin, conversation-memory, routing, and handoff suites.
- Ruff and `git diff --check`.
