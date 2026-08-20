# Log: semantic retrieval recovery

Date: 2026-08-13 15:30 CEST
Area: Cardine / tutor retrieval

## Summary

Added one bounded Luna-assisted recovery attempt for an unpinned explanation
whose initial lexical search returns no evidence. Luna may propose up to three
alternative queries from canonical source navigation vocabulary; Cardine uses
the first alternative that independently returns evidence. Successful searches,
explicit lesson pins, structural lesson matches, mutations, and flashcards do
not enter the recovery path.

The recovered query is applied inside a read-only retrieval adapter while the
capability keeps its original durable input identity. Provider vocabulary is
limited to current, non-retired sources that satisfy the same trust and role
policy as the search itself.

## Files Changed

- `src/cardine/adapters/model/retrieval_query_recovery.py`: closed structured-output adapter.
- `src/study_agent/prompts/retrieval_query_recovery_v1.py`: versioned recovery instruction.
- `src/study_agent/prompts/__init__.py`: prompt export.
- `src/cardine/cli/repository.py`: empty-search preflight and one-shot recovery.
- `tests/integration/demo/TUT08/test_semantic_retrieval_recovery.py`: public chat regressions.

## Verification

- Baseline red proof in a clean temporary checkout: 1 failed, 1 passed; the empty-query turn terminated.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_semantic_retrieval_recovery.py`: 2 passed.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/cli/test_live_lesson_recovery_contract.py tests/unit/cli/test_chat_attached_lesson_pin.py tests/integration/demo/TUT08/test_routing_recovery.py`: 9 passed.
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08 tests/unit/cli tests/unit/hosts tests/unit/demo` outside the macOS sandbox: 298 passed, 1 pre-existing provider-unavailable failure in `test_full_product_closure.py`.
- Focused AnyDoc integration outside the sandbox: PDF import and mixed-page worker tests passed.
- `MYPYPATH=src .venv/bin/python -m mypy -p cardine.adapters.model -p cardine.cli.repository`: passed.
- `PYTHONPATH=.:src .venv/bin/python -m ruff check ...`, `node --check src/cardine/demo/browser.js`, and `git diff --check`: passed.

## Notes

- This is deliberately not a generic autonomous-agent loop: there is one
  recovery model call and at most three read-only lexical probes.
- Claude's attached-lesson UI and backend work were preserved and verified as a
  separate slice in the same worktree.
