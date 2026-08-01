# Log: TUT-08B provider-backed tutor chat

Date: 2026-07-30 19:00
Area: adaptive-tutor / product shell

## Summary

Implemented the closed tutor-decision adapter over the existing
OpenAI-compatible model port, versioned its prompt, composed the trusted
repository tutor host, and replaced repository chat's direct grounding call
with `ConversationTurnApplication`.

Cardine now joins canonical tutor presentations into its private session DTO,
restores safe pending-continuation descriptors after repository restart, and
posts continuation responses through the transport-independent application.
The existing `grounding.ask`/study-tools seam remains intact; TUT-08C owns
effect-bearing capability completion handlers.

The review closeout tightened five invariants: explain continuations now bind
profile/source/index state rather than unrelated conversation sequence;
pending work accepts only its exact dialogue decision; genuine completed
capabilities return status-only; later decisions receive bounded canonical
presentation history; and conversation composition requires the runner's exact
continuation-store object.

## Files Changed

- `src/study_agent/adapters/model/tutor_decision.py`: closed local decision
  validation and safe provider-error mapping.
- `src/study_agent/prompts/tutor_decision_v1.py`: versioned bounded host policy.
- `src/study_agent/cli/repository.py`: DeepSeek/model-port tutor runner,
  authority, identity, continuation store, and request-bound explain gateway.
- `src/study_agent/demo/ui_application.py`: conversation and continuation
  command binding plus canonical presentation DTO join.
- focused adapter, repository, browser-asset, and integration tests.

## Verification

- Focused chat/host/repository regression suite after review fixes:
  `80 passed`.
- Adapter plus repository-backed HTTP suite after the provider-schema fix:
  `16 passed`.
- Ruff: passed.
- `git diff --check`: passed.
- Live synthetic DeepSeek smoke: a clean repository/session using only the
  packaged public fixture returned a canonical learner-question presentation,
  advanced the course stream from sequence 8 to 10, and returned the joined
  learner/tutor timeline.

## Notes

- The live smoke used no personal repository content. The configured key stayed
  in the environment and was not logged or persisted.
- Generic completed capability output is intentionally not copied into tutor
  speech. ADR-0016/TUT-08C owns the closed completion handoff.
