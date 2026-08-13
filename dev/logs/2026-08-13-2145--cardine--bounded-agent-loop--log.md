# Log: bounded agent loop

Date: 2026-08-13 21:45 CEST
Area: Cardine / tutor runtime

## Summary

Cardine now lets Luna observe a compact harness-tool result and choose the next
action in the same turn. Exact repeated calls are skipped, failures remain
observable to the next decision, and the existing four-decision budget remains
the hard stop. Tool-informed capability handoffs persist the exact bounded
observations in a backward-compatible v2 record.

## Files Changed

- `src/cardine/hosts/runner.py`: bounded observe-decide loop, duplicate guard,
  observation privacy bounds, and v1/v2 durable handoff codec.
- `src/cardine/cli/repository.py`: bind tool idempotency to the complete validated
  invocation fingerprint.
- `src/study_agent/prompts/tutor_decision_v1.py`: prompt 1.4.0 agent-loop rules.
- `tests/integration/demo/TUT08/test_bounded_agent_loop.py`: public same-turn
  observation and duplicate regressions.
- Existing host, prompt, and repository-chat tests were updated for the
  model-authored post-tool response contract.

## Verification

- Focused agent/routing/retrieval/handoff suite: 143 passed.
- Broader repository chat slice excluding two documented unrelated cases:
  27 passed, 2 socket skips.
- Focused mypy: passed.
- Ruff and `git diff --check`: passed.

## Notes

- No classifier, generic SDK agent, dependency, schema table, or unbounded loop
  was added.
- A crash after a tool completes but before the next decision still relies on
  the tool's idempotency during an exact turn retry; a separate loop checkpoint
  remains intentionally out of scope.
- Existing unrelated failures remain the sandboxed PDF conversion worker and
  process-local Tool Chips replay parity after application restart.
