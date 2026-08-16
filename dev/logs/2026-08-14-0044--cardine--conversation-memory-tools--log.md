# Log: conversation memory tools

Date: 2026-08-14 00:44 CEST
Area: Cardine / tutor runtime

## Summary

The bounded Luna agent can now detect an incomplete recent conversation,
search or page older canonical learner/assistant turns, observe those excerpts
within the same turn, and then start source-grounded flashcard generation.

The implementation keeps conversation as scope context rather than evidence.
Raw excerpts are ephemeral and are stripped from durable v3 handoffs; exact
retry retains only the final bounded capability inputs and a host-normalized
topic sketch. Diagnostics now record the post-routing structural decision
trajectory rather than the model's pre-router guess.

## Files Changed

- `src/cardine/application/conversation_history.py`: bounded canonical search
  and cursor reads.
- `src/cardine/application/tool_surface.py`: private conversation tool manifests
  and high-water-bound execution.
- `src/cardine/hosts/context.py`: complete/recent conversation-window metadata.
- `src/cardine/hosts/flashcard_routing.py`: tool-first broad-history route and
  normalized memory-informed flashcard summary.
- `src/cardine/hosts/runner.py`: post-router trajectory recording and v3 durable
  receipt/replay split for ephemeral observations.
- `src/cardine/diagnostics/turn_trace.py`: bounded four-step structural traces.
- `src/study_agent/prompts/tutor_decision_v1.py`: prompt 1.5.0 environment rules.
- `src/study_agent/tools/schema.py`: fail-closed string `maxLength` support.
- Focused unit, contract, integration, and TUT-08 tests cover the behavior and
  privacy boundary.

## Verification

- Final focused conversation/tool/prompt/router/handoff/flashcard suite:
  102 passed.
- Additional exact high-water/tool-loop slice: 51 passed.
- Source-grounding trajectory regression: passed.
- Ruff on all changed functional/test files: passed.
- `git diff --check`: passed.
- Focused mypy invocation reached two pre-existing errors in
  `src/cardine/diagnostics/turn_activity.py`; the new conversation reader type
  error found during the run was corrected.

## Notes

- The live server is restarted after final verification to load this backend
  change together with the preserved concurrent source-viewer changes.
- Concurrent source-viewer files and memory were preserved without edits by
  this work.
- Structural traces are process-local diagnostics, not yet an RL dataset. A
  future semantic export needs explicit consent, pseudonymization, versioned
  prompt/tool/model metadata, and learner-outcome labels.
