# Log: latest live turns do not execute flashcard work

Date: 2026-08-14 18:36 CEST
Area: Cardine / tutor orchestration

## Summary

Audited the latest canonical `session-live` turns after the learner reported
that Cardine kept answering without doing the requested work. The provider is
responding, but the four latest flashcard requests never enter tool or
capability execution. Luna returns an `assistant_message` promise; the
host-owned flashcard router fails to recognize each live wording; the runner
then treats the assistant message as a terminal successful presentation.

This is a new failure mode after the earlier deictic lesson-scope failures.
Events 176--178 did select `propose_flashcards` and persisted stale handoffs.
Events 183--190 do not persist any new capability run or completion handoff at
all.

## Live Evidence

- Event 183 asks `voglio che generi 15 flashcards ...`; event 184 promises that
  Cardine can prepare them, with no capability identity.
- Event 185 contains the typo `falshcards`; event 186 repeats the promise.
- Event 187 says `non mi rispondere, genera le cards`; event 188 asks another
  learner question instead of executing.
- Event 189 explicitly asks Cardine to use conversation tools and make the
  cards; event 190 again says it is preparing them, but no tool or capability
  run is recorded.
- `playbook_runs` remains at row 48. The last flashcard handoff is row 44,
  observed sequence 178, and is stale. There is no run attributable to
  sequences 183, 185, 187, or 189.
- The provider is not unavailable: assistant presentations were produced in
  about 3--7 seconds for the latest four requests.

## Root Cause

`src/cardine/hosts/flashcard_routing.py` recognizes only a narrow verb/card
surface. It does not cover Italian subjunctive `generi`, imperative `fai`, or
the live `falshcards` typo. Its 32-character negation scan also interprets the
`non` in `non mi rispondere, genera le cards` as negating card generation even
though it negates the conversational response.

When recognition returns false, the wrapper preserves Luna's validated
`AssistantMessageDecision`. `TutorHostRunner` immediately returns that message
as `ASSISTANT_MESSAGE`; it performs no semantic promise rejection and no
capability retry. Prompt 1.6.0 explicitly forbids promises, but that instruction
is not an enforced runtime invariant.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/hosts/test_flashcard_routing_natural_language.py`: 9 passed; existing tests omit all four live formulations.
- Red-capable wrapper probe with `propose_flashcards` advertised and the exact
  four live messages: four `AssistantMessageDecision` results, zero
  `StartCapabilityDecision` results, followed by assertion failure
  `latest live requests are not routed to flashcard execution`.
- Direct recognizer probe: all four live messages return `False`.
- `curl -sS --max-time 3 -i http://127.0.0.1:8765/health`: connection refused;
  the previously local live process was not running during this audit, so
  process-local `/api/v1/diagnostics/turns` evidence could not be recovered.

## Recommended Fix Boundary

- Add the exact four live messages as failing routing regressions.
- Make intent recognition cover ordinary Italian morphology, the observed
  typo, and clause-local negation without broadening flashcard meta-questions.
- Add a host invariant that rejects or recovers an assistant-message promise
  when an explicit advertised capability request is present; prompt wording
  alone is insufficient.
- Verify that the exact live sequence creates a flashcard capability run and a
  reviewable proposal batch, not merely a different chat response.

No production code or canonical live state was changed during this audit.
