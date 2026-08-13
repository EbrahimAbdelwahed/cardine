# Log: Linear FTS rebuild for large canonical sources

Date: 2026-08-12 19:13
Area: cardine-retrieval

## Summary

Removed two multiplicative catalog scans from the complete SQLite FTS rebuild.
The rebuild now validates each document against one immutable chunk lookup, and
the Cardine catalog loads retired source identifiers once per course and
snapshot rather than once per chunk.

The already-admitted 559-page Biochimica PDF was rebuilt successfully without
reconversion or re-upload: 3,676 canonical chunks were indexed in 4.18 seconds.

## Files Changed

- `src/study_agent/adapters/sqlite/fts_retrieval.py`: reuse the materialized
  canonical catalog during complete rebuild validation.
- `src/cardine/cli/repository.py`: load retired source identifiers once per
  course when materializing a catalog snapshot.
- `tests/contract/retrieval/test_sqlite_fts_contract.py`: large-catalog rebuild
  regression.
- `tests/unit/cli/test_repository.py`: source-lifetime lookup regression through
  the public repository rebuild seam.

## Verification

- `PYTHONPATH=.:src .venv/bin/pytest -q tests/contract/retrieval/test_sqlite_fts_contract.py tests/integration/test_fts_retrieval.py tests/unit/cli/test_repository.py`: 47 passed.
- `.venv/bin/ruff check ...`: passed on the four changed source/test files.
- `MYPYPATH=src .venv/bin/python -m mypy --explicit-package-bases ...`: passed on the four changed source/test files.
- `git diff --check`: passed.
- Real `LocalRepository.rebuild_retrieval()` against `cardine-wave-a-live`: 3,676 chunks indexed in 4.18 seconds.

## Notes

- Canonical source events and blobs were not rewritten.
- The rebuild remains atomic and the derived index contains no partial state.
