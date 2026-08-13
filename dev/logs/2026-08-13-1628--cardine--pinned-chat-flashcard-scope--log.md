# Log: pinned chat flashcard scope

Date: 2026-08-13 16:28 CEST
Area: Cardine / chat flashcards

## Summary

Diagnosed the live 16:13 turn that requested a flashcard and produced only a
generic error. Routing correctly selected `propose_flashcards`, but the chat
gateway ignored the attached lesson pin and built a flashcard plan from all
3,676 current chunks. The planner rejects more than 256 lesson-index entries,
so generation failed before a proposal worker or artifact was created.

The gateway now calls the existing pin-scoped flashcard composition whenever a
validated lesson is attached. Unpinned flashcard behavior is unchanged.

## Files Changed

- `src/cardine/cli/repository.py`: route pinned chat flashcard requests through `start_for_pin`.
- `tests/integration/demo/TUT08/test_flashcard_proposals.py`: prove a pinned chat turn succeeds and excludes other lessons even when the course contains 260 sections.

## Verification

- Red proof: the new public chat test failed with `model provider is unavailable` before the gateway change.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_flashcard_proposals.py tests/unit/cli/test_chat_attached_lesson_pin.py tests/unit/cli/test_selected_lesson_flashcards.py`: 14 passed.
- `.venv/bin/python -m ruff check src/cardine/cli/repository.py tests/integration/demo/TUT08/test_flashcard_proposals.py`: passed.
- `git diff --check`: passed.

## Notes

- The requested singular quantity (`una flashcard`) is not an exact-count contract today; the capability may produce more than one proposal for multiple canonical chunks. Exact quantity handling is separate from this no-output fix.
- The live server was not restarted from this side task. The shared worktree contains concurrent Tool Chips changes that were preserved.
