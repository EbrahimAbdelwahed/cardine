# Log: repeated deictic flashcard live failure

Date: 2026-08-14 14:38 CEST
Area: Cardine / flashcard generation

## Summary

The live retry of `genera 15 flashcards su questa lezione` still failed even
though the tutor selected `propose_flashcards` in Italian. The previous fix
works for a clean sequence consisting of an explicit lesson turn followed by
one deictic request, but not when the learner repeats the deictic request after
that request has already failed.

The bounded reverse scan treats any previous human message containing
`lezione` as the nearest explicit lesson reference. In the live history it
therefore stops on the immediately preceding failed request, which is itself
`genera 15 flashcards su questa lezione`. Structural resolution of that
deictic text returns no pin, and the scan returns immediately instead of
continuing to the earlier canonical `leggi lezione 1 biochimica` turn.

Without a pin, flashcard generation falls back to the all-course planner. The
active source has 3,676 chunks while the planner accepts at most 256 entries,
so the capability fails before producing a flashcard run. Its generic failure
mapping labels this deterministic planning error as `unavailable`; the browser
therefore reports the misleading `tutor_unavailable` 503.

## Live Evidence

- Event 174: `leggi lezione 1 biochimica`; event 175 is the completed grounded
  Lezione 1 presentation.
- Events 176 and 177: repeated `genera 15 flashcards su questa lezione` learner
  turns with no assistant presentation after either one.
- Latest durable handoff: `propose_flashcards`, language `it`, state `stale`,
  with no flashcard capability run created.
- Snapshot probe: explicit Lezione 1 resolution is true while full deictic pin
  recovery is false.
- Control probe: removing only the preceding failed duplicate makes deictic
  pin recovery true.

## Verification

- Red-capable live snapshot probe:
  `explicit_pin=True`, `deictic_pin=False`, assertion failure
  `latest live deictic request lost its lesson pin`.
- Single-variable control probe:
  `removed_previous_failed_duplicate=True`, `deictic_pin=True`.

## Notes

- This is not a Luna routing failure: the validated action is already
  `propose_flashcards` and Italian detection is correct.
- The previous regression omitted the retry topology. A repair must distinguish
  an explicit lesson reference from a deictic mention and cover two consecutive
  identical deictic requests after a successful explicit lesson turn.
- The capability failure taxonomy should separately stop mapping every local
  planning `ValueError` to provider `unavailable`, but that observability repair
  is secondary to restoring the lesson pin.
