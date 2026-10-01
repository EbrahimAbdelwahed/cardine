# Log: material and lineage contracts

Date: 2026-08-14 17:21 CEST
Area: Cardine / materials

## Summary

Implemented Slice 01 of the approved sbobina material-generation workflow. The
change introduces blob-backed complete/study lesson-material artifacts and a
strict generated-source v2 admission boundary. It does not add provider calls,
generation jobs, browser routes or UI.

The generated-source boundary is SERVICE-only and checks the current canonical
projection before append. Replay, export and retrieval resolve the exact root,
accepted artifact, HUMAN decision and complete/study lineage. Generated
Markdown content and headings remain blob-only.

## Files Changed

- `src/study_agent/domain/`: lesson-material vocabulary and generated lineage.
- `src/study_agent/artifacts/content.py`: bounded blob-referencing material codec.
- `src/study_agent/ingestion/`: v2 identity, codec, stateful reducer and shared
  text preparation.
- `src/cardine/materials/`: privileged generated-source materializer contract.
- `src/study_agent/application/export.py`, `src/study_agent/retrieval/content.py`,
  `src/study_agent/tutor_snapshot/reader.py`: stateful v2 consumers.
- `tests/unit/artifacts/test_lesson_material_content.py`,
  `tests/unit/ingestion/test_generated_source_revision_state.py`, and
  `tests/integration/test_generated_source_materializer.py`: focused behavior
  and adversarial regression coverage.

## Verification

- `.venv/bin/python -m pytest -q <focused artifact/source/export/retrieval/tutor tests>`:
  73 passed.
- `.venv/bin/ruff check <touched slice files>`: passed.
- `.venv/bin/python -m mypy <15 touched source modules>`: passed.
- `.venv/bin/python -m compileall -q <touched packages>`: passed.
- `git diff --check`: passed.
- Independent security re-review: no residual blockers.
- `.venv/bin/python -m pytest -q`: 2,367 passed, 31 skipped, 27 failed. The
  failures are outside Slice 01: stale CLI/discovery and host snapshots from
  concurrent dirty-worktree changes, sandbox-denied local sockets, sandboxed
  AnyDoc worker protocol failures, an existing browser tooltip assertion and
  parity/golden drift.

## Notes

- Existing unrelated dirty-worktree changes were preserved.
- No dependencies, provider calls, commits or pushes were introduced.
- Stop here. Slice 02 requires explicit user authorization.
