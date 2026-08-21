# Log: live flashcard lesson-scope failure

Date: 2026-08-14 01:29 CEST
Area: Cardine / flashcard generation

## Summary

The live turn `genera 15 flashcards su questa lezione` consistently returned
HTTP 503 after the tutor had successfully explained lesson 1. Canonical state
shows that routing did select `propose_flashcards`; the failure happened inside
flashcard capability setup, before a generation run was created.

The request carried no usable current-turn lesson pin and its fallback inputs
kept the deictic text `questa lezione`. The generic flashcard composition then
built a lesson plan from every active chunk in the course source. The live PDF
contains 3,676 chunks, while `FlashcardLessonPlan` allows at most 256 index
entries, so plan construction raises `lesson_index_limit_exceeded` before the
generation model is called.

## Evidence

- Canonical sequence 176 records the learner request and no corresponding tutor
  presentation.
- The durable handoff at observed sequence 176 selected
  `propose_flashcards`, then became `stale`.
- No new flashcard generation run was persisted for that turn.
- A read-only planner invocation over the live repository reproduced the exact
  failure with one active revision and 3,676 chunks.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -c '<read-only live _lesson_plan probe>'`:
  `ValueError: lesson_index_limit_exceeded` in about one second.
- Server stderr recorded two matching
  `status=503 category=tutor_unavailable` results.

## Notes

- This is not primarily a provider outage or a model-routing failure. The
  learner-safe `tutor_unavailable` category hides a deterministic scope error.
- The smallest product repair is to carry or recover the last uniquely resolved
  lesson for deictic flashcard requests and execute `start_for_pin`; the generic
  all-course planner should also fail with a truthful scope clarification rather
  than HTTP 503.
- No production code was changed during this diagnosis.
