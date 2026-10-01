# Log: Page-aware flashcard locator fix

Date: 2026-08-15 00:40
Area: cardine

## Summary

Diagnosed the repeated live flashcard failure as a deterministic planning/evidence mismatch before Luna generation. The tutor selected `propose_flashcards` correctly, but `_lesson_plan()` persisted a handwritten locator without PDF page provenance while canonical evidence resolution added `page 3`; exact integrity validation therefore stopped the worker in `resolving`.

Centralized canonical locator construction in the retrieval layer and reused it from both lesson planning and citation resolution. Exact locator validation remains fail-closed. Historical malformed runs are unchanged; a fresh turn receives a corrected plan fingerprint.

## Files Changed

- `src/study_agent/retrieval/content.py`: expose the single canonical source-locator constructor and reuse it during citation resolution.
- `src/study_agent/retrieval/__init__.py`: export the canonical constructor.
- `src/cardine/application/flashcard_proposals.py`: build planned spans with the canonical constructor instead of duplicated formatting.
- `tests/unit/cardine/test_a2_regressions.py`: pin page-aware planning through evidence resolution.

## Verification

- `.venv/bin/pytest -q tests/unit/cardine/test_a2_regressions.py::test_page_aware_flashcard_plan_uses_the_canonical_resolved_locator`: `1 passed`.
- `PYTHONPATH=. .venv/bin/pytest -q tests/unit/cardine/test_a2_regressions.py tests/unit/flashcards/test_lesson_worker_service.py tests/unit/flashcards/test_lesson_worker_contracts.py tests/contract/source_content/test_source_content_contract.py tests/integration/test_source_content_resolution.py tests/integration/test_lesson_worker_recovery.py tests/integration/demo/TUT08/test_flashcard_proposals.py`: `88 passed`.
- Live-data deterministic preparation replay with a fresh scoped plan: planned and resolved locators were byte-identical and `_LessonEvidenceResolver` returned `GREEN resolved_items=1 slots=1`.
- `.venv/bin/ruff check src/study_agent/retrieval/content.py src/study_agent/retrieval/__init__.py src/cardine/application/flashcard_proposals.py tests/unit/cardine/test_a2_regressions.py`: passed.
- `git diff --check`: passed.
- Independent semantic review of the locator-specific hunks: no findings.
- `launchctl kickstart -k gui/$(id -u)/com.cardine.local-preview`: live server restarted; `/api/v1/settings` returned `mode=local_repository` and `credential_configured=false` as expected for a process-local credential after restart.

## Notes

- The visible `tutor_unavailable` classification was misleading: generic deterministic worker exceptions currently collapse to `unavailable`. Correcting that broader error taxonomy is outside this minimal fix.
- A server restart is required to load the code. Because the API key is deliberately process-local and non-persistent, it must be entered again in Settings after restart.
