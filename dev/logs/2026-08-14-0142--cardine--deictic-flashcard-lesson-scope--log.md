# Log: deictic flashcard lesson scope

Date: 2026-08-14 01:42 CEST
Area: Cardine / flashcard generation

## Summary

`Genera 15 flashcard su questa lezione` now reuses the nearest recent,
uniquely resolved lesson reference in the same canonical session. The gateway
keeps an attached UI pin strongest, next accepts an explicit current lesson
query, and consults at most the previous twelve human turns only when the
original current learner message is deictic.

The resolved fresh pin is passed to the existing `start_for_pin` path. Cardine
therefore plans and grounds cards from that lesson instead of attempting a
3,676-chunk all-course plan. Italian detection now also covers the live
`genera ... questa lezione` wording.

## Files Changed

- `src/cardine/cli/repository.py`: bounded recent lesson-scope recovery and
  pinned flashcard dispatch.
- `src/cardine/hosts/flashcard_routing.py`: Italian wording recognition.
- `tests/integration/demo/TUT08/test_flashcard_proposals.py`: public-turn
  regression over 260 lessons, including a model-normalized deictic query.

## Verification

- Regression before fix: HTTP-facing test failed with model-provider
  unavailable after the global planner exceeded 256 entries.
- Regression after fix: passed; generated prompt includes Lezione 1 and excludes
  Lezione 2.
- Focused flashcard, memory, routing, handoff, attached-pin, and lesson recovery
  suites: 83 passed.
- Ruff and `git diff --check`: passed.
- Independent semantic review: no remaining HIGH/MEDIUM findings.

## Notes

- No persistent cross-session pin was added.
- Ambiguous or stale nearest lesson references still fail closed.
- Concurrent browser/source-viewer work remains untouched.
