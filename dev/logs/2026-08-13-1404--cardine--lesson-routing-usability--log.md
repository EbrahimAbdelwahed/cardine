# Log: Lesson routing usability

Date: 2026-08-13 14:04 CEST
Area: Cardine / tutor routing / structural retrieval

## Summary

The live `studiamo la lezione 1` failure is repaired without adding a classifier
or a general agent loop. Luna remains the primary closed decision policy; the
host prevents an explicit lesson-study request from terminating as a promise.

Natural `Lezione 1` references now resolve mechanically converted headings such
as `L01_04/03/2025`, and structural evidence is resolved through the canonical
content adapter before it reaches the explanation model.

## Files Changed

- `src/cardine/hosts/source_grounding.py`: recognize an explicit lesson
  reference combined with natural study intent while preserving social mentions.
- `src/cardine/knowledge/lesson_selection.py`: define bounded equivalence between
  natural and mechanically converted lesson titles.
- `src/cardine/cli/repository.py`: use that equivalence for structural navigation
  and resolve canonical citations for all in-range chunks.
- `src/study_agent/prompts/tutor_decision_v1.py`: document natural study intent
  and the social negative example in prompt version 1.3.1.
- Focused unit and integration tests pin the converted-title and exact live-chat
  journeys.

## Verification

- Focused routing, structural retrieval, prompt, and repository-backed chat:
  59 passed, 2 socket-sandbox skips; the unrelated AnyDoc PDF test remains the
  only failure in that command.
- Concurrent attached-lesson compatibility: 9 passed.
- Ruff and `git diff --check`: passed.
- Independent review found converted-identifier delimiter and broad `fare`
  false-positive risks; both were corrected with focused positive and negative
  routing coverage.
- Live repository validation resolves `Lezione 1` to `L01_04/03/2025` as a
  13,208-character range containing 23 complete canonical chunks. The fallback
  boundary stops at the next numbered lesson rather than the next mechanically
  extracted peer heading.

## Notes

- Semantic query retry after zero evidence remains intentionally deferred.
- Tool Chips and all browser presentation work remain in the separate UI lane.
- PageIndex `pageindex_limit` is not changed; deterministic Markdown navigation
  provides the required live fallback.
