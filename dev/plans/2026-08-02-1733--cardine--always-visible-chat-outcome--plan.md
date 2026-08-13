# Plan: always-visible chat outcome

Date: 2026-08-02 17:33
Area: Cardine tutor chat

## Goal

Guarantee that every terminal learner turn produces exactly one canonical,
learner-visible tutor presentation, including capability termination, runtime
failure, budget exhaustion, and completion-recovery failure. Preserve
`IN_PROGRESS` as a retryable operational state rather than settling it early.

## Scope

- In scope:
  - conversation application fallback presentations;
  - exact-retry behavior for fallback outcomes;
  - repository-backed HTTP/UI regression coverage;
  - safe, non-sensitive Italian fallback copy.
  - Cardine-owned injection of that copy into the application settlement seam.
- Out of scope:
  - changing the provider decision schema;
  - changing capability execution semantics;
  - exposing internal failure details to learners;
  - redesigning the generic host runner.

## Approach

1. Add failing application tests for every host outcome that currently leaves
   a persisted learner turn without a presentation.
2. Persist a deterministic assistant-message fallback at the conversation
   boundary while retaining the original technical status in the terminal
   receipt.
3. Make exact retries return the same fallback presentation without repeating
   model or capability work.
4. Add a repository/UI response regression and run focused then broad checks.
5. Keep `IN_PROGRESS` retryable with no terminal journal entry.

## Risks

- A fallback must not leak provider or capability failure details.
- Presentation and terminal receipt persistence must remain idempotent across
  crashes and retries.
- A pending continuation must not remain active after a terminal fallback.

## Verification

- `.venv/bin/python -m pytest -q tests/integration/test_conversation_turn_application.py`
- `.venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_repository_backed_chat.py`
- `.venv/bin/ruff check <changed files>`
- `.venv/bin/mypy <changed source and tests>`
- `git diff --check`
