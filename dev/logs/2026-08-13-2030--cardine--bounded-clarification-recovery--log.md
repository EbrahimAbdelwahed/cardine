# Log: bounded clarification recovery

Date: 2026-08-13 20:30 CEST
Area: Cardine / tutor routing

## Summary

Implemented the targeted repair for the live circular clarification dialogue.
Tutor decision prompt `tutor_decision.v1` is now version 1.3.2 and explicitly
treats a learner answer to the latest tutor question as resolved context.

A new decision-port wrapper performs at most one semantic retry when Luna still
returns `ask_learner` immediately after the learner answered the latest tutor
question. The retry context adds only the bounded previous question, current
answer, and static recovery guidance. It does not classify topics, force a
capability, add persistent state, or create a general agent loop. If the second
decision remains `ask_learner`, Cardine accepts it. If the retry fails, Cardine
falls back to the first valid question.

## Files Changed

- `src/cardine/hosts/clarification_recovery.py`: one bounded semantic decision retry.
- `src/cardine/hosts/__init__.py`: exports the wrapper.
- `src/cardine/cli/repository.py`: composes the wrapper inside the existing routing stack.
- `src/study_agent/prompts/tutor_decision_v1.py`: prompt 1.3.2 follow-up contract and examples.
- `tests/integration/demo/TUT08/test_routing_recovery.py`: public conversation regression.
- `tests/unit/adapters/model/test_tutor_decision.py`: prompt version and guidance contract.
- `dev/plans/2026-08-13-2010--cardine--bounded-clarification-recovery--plan.md`: scoped plan.

## Verification

- Red proof: focused live-pattern integration test returned
  `needs_learner_input` before implementation.
- Green proof: the same test completed with exactly one extra
  `tutor_decision.v1` call and then `explain_concept.v1`.
- Focused routing/retrieval/pin suite: 40 passed.
- Broader repository and host slice: 113 passed, 2 socket skips, 2 unrelated
  pre-existing failures (PDF AnyDoc worker and restarted Tool Chips activity receipt parity).
- Ruff: passed for all changed Python files.
- Mypy with explicit package bases: new wrapper and prompt passed. The complete
  repository check still reports pre-existing typing errors in Tool Chips and
  other concurrent Wave A files.
- `git diff --check`: passed.

## Notes

- No live canonical conversation events were mutated during diagnosis or testing.
- The next optional improvement is a compact chronological decision-context tail;
  it is not required for this fix and remains out of scope.
