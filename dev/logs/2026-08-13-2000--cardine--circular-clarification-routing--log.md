# Log: circular clarification routing

Date: 2026-08-13 20:00 CEST
Area: Cardine / tutor routing / live diagnosis

## Summary

The live conversation was inspected read-only after the learner reported a
circular dialogue. The symptom is canonical and reproducible: course
sequences 142 through 151 contain five learner turns followed by five
consecutive `learner_question` presentations. The learner progressively chose
`acetilazione`, `meccanismo`, `voglio capire gli effetti sulla proteina`, and
`negli istoni`, but the tutor kept asking another version of the same
clarification instead of explaining the topic.

This is not a missing explain capability. It is a clarification-state and
context-shape failure:

- `AskLearnerDecision` immediately returns `NEEDS_LEARNER_INPUT`; it does not
  create a typed pending continuation. The next turn therefore starts routing
  from scratch.
- The prompt permits `ask_learner` when the goal is ambiguous but has no rule
  saying that an answer to the tutor's immediately preceding question resolves
  that ambiguity, and no bound preventing consecutive clarification turns.
- Learner interactions and tutor presentations reach the model in separate
  collections. The live projection contains 74 interactions and 73 tutor
  presentations; the presentation collection alone is about 116 KB. A short
  answer must therefore be correlated with a prior question through opaque IDs
  inside a large history.
- The source-grounding safety net only recognizes explicit explanation/study
  wording. Short follow-ups such as `meccanismo` and `negli istoni` do not match,
  so the model's repeated `ask_learner` decision is left unchanged.

## Files Changed

- `dev/logs/2026-08-13-2000--cardine--circular-clarification-routing--log.md`:
  durable live diagnosis only; no production code changed.
- `dev/index.md`: added this diagnosis to the current Cardine entrypoints.

## Verification

- `sqlite3 -json ../cardine-wave-a-live/state/events.sqlite3 ... | jq -e ...`:
  returned `true` for exactly five consecutive `learner_question` outcomes in
  sequences 142-151.
- Read-only projection query: 74 interaction records, 73 tutor presentations,
  approximately 24 KB and 116 KB respectively.
- Source inspection confirmed the immediate `AskLearnerDecision` terminal in
  `src/cardine/hosts/runner.py`, separated presentation serialization in
  `src/cardine/hosts/context.py`, and the explicit-word matcher in
  `src/cardine/hosts/source_grounding.py`.

## Recommended Targeted Fix

1. Add a prompt contract: the learner's reply to the immediately preceding
   `learner_question` resolves that clarification; do not emit a second
   clarification for the same goal. Select the capability or answer now.
2. Supply a compact, chronologically interleaved recent conversation tail to
   the decision model, while retaining canonical history server-side. This is
   presentation shaping, not a new state store.
3. Add one bounded recovery: if Luna nevertheless emits consecutive
   `ask_learner` decisions after the learner answered the prior question, retry
   the decision once with the resolved latest exchange made explicit. Do not
   add regex topic classification or a generic agent loop.

The prompt plus compact recent tail should be tested first. The single retry is
the fail-safe and should remain bounded to one extra decision.
