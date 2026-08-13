# Handoff: bounded clarification recovery

Date: 2026-08-13 20:30 CEST
Area: Cardine / tutor routing

## Current State

Answered tutor clarifications now receive one bounded semantic recovery instead
of immediately repeating the same question. The fix is prompt-driven and uses
Luna's existing natural-language decision ability; no classifier or general
agent runtime was added.

## Completed

- Prompt `tutor_decision.v1` version 1.3.2.
- Explicit no-repeat rule for answered clarifications.
- One fail-safe decision retry with a bounded safe exchange.
- Public conversation regression for the live `negli istoni` pattern.

## Remaining

- Validate the behavior with new turns on the live server.
- Consider a compact interleaved recent conversation tail only if long-session
  routing remains weak; do not fold that larger context-shaping change into this fix.

## Important Context

- The retry never forces `explain_concept`; Luna's second validated decision is authoritative.
- Genuine ambiguity may still result in `ask_learner`, but no third decision is attempted.
- Existing retrieval-query recovery is independent and unchanged.
- Root recovery bundles remain untracked and must not be committed.

## Verification

- Focused regression: 1 passed after confirmed red failure.
- Routing/retrieval/pin suite: 40 passed.
- Broader slice: 113 passed, 2 skips, 2 documented unrelated failures.
- Ruff, focused mypy, and diff check: passed.
