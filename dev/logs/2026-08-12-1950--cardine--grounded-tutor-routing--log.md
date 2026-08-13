# Log: Grounded tutor routing

Date: 2026-08-12 19:50
Area: cardine / grounded tutor

## Summary

Diagnosed the live Biochimica conversation from canonical events and playbook
runs. Retrieval was healthy and the latest explain-concept run read eight
canonical evidence items, but its useful draft was terminated because a
`study_guidance` segment carried an evidence identifier. Earlier natural
requests such as “avvia una spiegazione” and “parliamo di” were left as bare
assistant acknowledgements instead of starting the grounded capability.

Expanded the host-side Italian study-request vocabulary, removed routing words
from lexical queries, prohibited future-work promises in the tutor decision
instruction, and versioned the explain-concept prompt to 1.1.0 with the exact
segment semantics enforced by the existing grounding validator. PageIndex was
not changed and remains navigation-only.

## Files Changed

- `src/cardine/hosts/source_grounding.py`: route additional natural Italian
  explanation requests to `explain_concept` and retain only content terms.
- `src/study_agent/prompts/tutor_decision_v1.py`: require immediate capability
  selection instead of an assistant promise.
- `src/study_agent/prompts/explain_concept_v1.py`: document exact grounded
  segment kinds and bump the prompt artifact to 1.1.0.
- `tests/unit/hosts/test_source_grounding.py`: natural-language routing
  regressions.
- `tests/unit/prompts/test_explain_concept_prompt.py`: prompt/validator contract
  regression.
- `tests/unit/adapters/model/test_tutor_decision.py`: system-prompt regression.

## Verification

- Focused red phase: 4 failures on the missing routing and prompt rules.
- Focused green phase: 19 passed.
- Capability, grounding, tool and end-to-end matrix: 61 passed.
- Ruff: passed on all changed source/test paths.
- Mypy: passed on all changed source/test paths.
- `git diff --check`: passed.

## Notes

- The live server must be restarted to load these Python changes.
- A browser `409 stale_sequence` is a separate optimistic-concurrency response;
  after the UI refreshes its sequence, the learner must submit the command
  again.
