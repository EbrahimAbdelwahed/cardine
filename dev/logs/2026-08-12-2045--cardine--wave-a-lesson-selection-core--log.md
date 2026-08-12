# Log: Wave A lesson selection core

Date: 2026-08-12 20:45
Area: cardine-knowledge

## Summary

Added pure Cardine navigation contracts for deterministic lesson search and complete source pins. Markdown headings are scanned with bounded fence-aware ATX rules; plain text candidates require exact injected canonical evidence spans.

## Files Changed

- `src/cardine/knowledge/lesson_selection.py`: immutable source/chunk/candidate/pin DTOs, explicit ambiguity result, selection, and stale validation.
- `src/cardine/knowledge/__init__.py`: narrow public Cardine knowledge surface.
- `tests/unit/cardine/knowledge/test_lesson_selection.py`: section boundaries, fenced headings, closing hashes, ambiguity, canonical evidence, and stale pins.

## Verification

- Focused pytest: 3 passed.
- Ruff: passed.
- Mypy: passed.
- `git diff --check`: passed.

## Notes

- This module has no Study Agent, PageIndex, repository, CLI, UI, or provider imports.
- PageIndex and lexical adapters consume this contract in the next pass; they cannot author canonical evidence.
