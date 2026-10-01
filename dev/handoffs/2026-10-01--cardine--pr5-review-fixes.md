# Handoff: PR #5 automatic review fixes

Date: 2026-10-01
Area: Cardine / flashcard routing and material validation

## Current state

Three current automatic review findings against PR #5 head
`8a4334350fcef0da4cf43027f2288256cd52b016` are implemented in an isolated,
Codex-managed worktree. The worktree is detached at the submitted head because
the existing PR branch is checked out in another owner-managed checkout. The
local fix commit is intended for integration into
`codex/cardine-wave-a-recovery`; this worktree did not move that branch or
publish the commit.

## Changes

- After a successful `conversation.search` or `conversation.read`, memory-scoped
  flashcard inputs now use a bounded topic sketch extracted from validated
  entries bound to the tutor snapshot high-water sequence. Invalid or empty
  history asks for a concrete topic. Canonical source chunks remain the only
  factual grounding for generated cards.
- Material validation now requires every recognized source emphasis marker to
  survive both merge and study output, and recognizes common Italian
  uncertainty and limitation wording.
- Added focused regressions for history-derived scope and fail-closed history,
  preservation of multiple emphasis markers, and accepted/rejected Italian
  uncertainty limitations. Updated the approved recovery digest for the
  audited flashcard router.

## Verification

- Focused unit and conversation-memory integration tests: 34 passed.
- Ruff: passed for the changed source and tests.
- Strict focused mypy: passed for both changed source modules.
- Harness ownership audit: OK (322 rows).
- `git diff --check`: passed.

The changes are offline and use scripted fixtures. No provider calls, preview
updates, push, review request, merge, or CI run occurred in this worktree.
