# Handoff: bounded agent loop

Date: 2026-08-13 21:45 CEST
Area: Cardine / tutor runtime

## Current State

The shared worktree contains a verified bounded observe-decide-act loop. After
an advertised harness tool, Luna sees a safe compact result and can answer,
choose another distinct tool, start a capability, or ask one clarification.

## Completed

- Same-turn tool-result observation.
- Exact duplicate tool suppression.
- Four-decision hard budget retained.
- Prompt `tutor_decision.v1` 1.4.0.
- Tool invocation-bound idempotency.
- Durable handoff schema v2 with byte-stable v1 decoding.

## Remaining

- Exercise natural multi-step turns against the live server.
- Consider a durable pre-capability loop checkpoint only if real crash evidence
  demonstrates a gap; do not add one speculatively.

## Important Context

- Observations are ephemeral operational context, not canonical learner memory
  or assistant-visible prose.
- Raw tool output, exception text, authority, credentials, and provider data are
  not persisted in observations.
- The lexical recovery and clarification recovery remain separate bounded
  policies and were not generalized or removed.

## Verification

- Focused suite: 143 passed.
- Repository chat slice: 27 passed, 2 socket skips.
- Ruff, focused mypy, and diff check: passed.
