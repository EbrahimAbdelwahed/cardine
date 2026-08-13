# Log: Luna routing and bounded retrieval query

Date: 2026-08-02 22:41
Area: Cardine tutor routing and retrieval

## Summary

Updated the versioned tutor decision prompt so conversational turns produce an
assistant message, grounded study workflows use capabilities, repository
operations use tools, and capability retrieval queries are concise lexical
queries rather than copies of learner prose. The operation catalog is derived
from the exact closed decision schema and fails closed when Cardine has no
versioned routing guidance for an advertised operation.

Updated SQLite FTS retrieval to prefer exact source titles and then use a
bounded, deterministic relevance fallback when strict literal AND matching is
too restrictive. Central retrieval query and result limits bound adapter work.

## Files Changed

- `src/study_agent/prompts/tutor_decision_v1.py`: versioned routing and query policy plus operation guidance.
- `src/study_agent/adapters/model/tutor_decision.py`: schema-aligned prompt catalog.
- `src/study_agent/adapters/sqlite/fts_retrieval.py`: exact-title-first and bounded relevance retrieval.
- `src/study_agent/ports/retrieval.py`: central query and limit bounds.
- `tests/unit/adapters/model/test_tutor_decision.py`: prompt routing/catalog regressions.
- `tests/evals/test_lexical_retrieval_fixtures.py`: natural-language, title, weak-match, and injection fixtures.
- `tests/contract/retrieval/test_sqlite_fts_contract.py`: central bounds contract.

## Verification

- `uv run pytest -q tests/unit/adapters/model/test_tutor_decision.py tests/evals/test_lexical_retrieval_fixtures.py tests/contract/retrieval/test_sqlite_fts_contract.py`: 43 passed.
- `uv run ruff check <changed files>`: passed.
- `uv run mypy <changed source files>`: passed.
- `uv run --frozen --extra dev python -m pytest -q`: 2163 passed, 13 skipped, 5 pre-existing failures outside this change (browser fixture expectation, schema/model error classification, and design-system accent usage).
- `uv run --frozen --extra dev python -m ruff check .`: passed.
- `uv run --frozen --extra dev python -m mypy`: 4 pre-existing errors in `playbooks/engine.py` and `demo/ui_application.py`.
- Real preview repository checks: concise and verbose Biochimica queries returned sufficient evidence; instruction-shaped and distributed weak queries remained insufficient.
- Independent correctness review: no remaining MEDIUM+ findings.
- Independent security review: no remaining MEDIUM+ findings.

## Notes

- No classifier dataset or training was added.
- Public tool/capability schema fingerprints were intentionally preserved; bounds are enforced at the central `RetrievalQuery` boundary.
- The shared retrieval change is propagated separately to Study Agent Harness.
