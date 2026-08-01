# Log: TUT-08E recall review flow

Date: 2026-07-30 16:15 CEST
Area: adaptive tutor / Cardine product shell

## Summary

Exposed the canonical recall composition in repository-backed Cardine UI. A
separate enrollment command now follows artifact acceptance with a stable,
domain-separated identity derived from course/session/revision. Due reads join
accepted current flashcard content with bounded answer and provenance DTOs.
Review routes strictly map the four learner ratings to `RecallService`, fence
stale commands before scheduling, and preserve exact retry recovery. The
browser keeps reveal state local and sends only enrollment/review commands.

## Files Changed

- `src/study_agent/demo/ui_application.py`: recall composition metadata, due
  DTO, enrollment/review routes, deterministic enrollment identity, and
  accepted-flashcard enrollment status.
- `src/study_agent/demo/browser.js`: enrollment action, unavailable recall
  state, local reveal, and canonical four-rating route wiring.
- `specs/adaptive-tutor/beads/TUT-08E-recall-review-flow.md`: acceptance
  criteria marked complete.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/recall tests/contract/recall tests/integration/test_recall_service.py tests/integration/test_recall_composition.py tests/integration/test_recall_ledger_replay.py tests/integration/test_recall_real_fsrs_e2e.py tests/e2e/test_cardine_browser_contract.py`: 42 passed, 5 skipped (optional `fsrs==6.3.1` and sandbox loopback).
- `PYTHONPATH=src:. python -m pytest -q tests/integration/demo/TUT08/test_repository_assessments_evidence.py tests/integration/demo/TUT08/test_repository_materials_artifacts_context.py tests/unit/demo/test_browser_assets.py tests/contract/recall`: 24 passed.
- `PYTHONPATH=src:. python -m pytest -q tests/integration/demo/TUT08/test_repository_recall_flow.py`: 9 passed.
- `python -m ruff check src/study_agent/demo/ui_application.py`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- Provider-neutral import check: passed; no `fsrs` module imported.
- `git diff --check`: passed.

The review follow-up also adds a bounded `next_schedule` receipt to successful
review responses, refreshes browser bootstrap counts after recall commands, and
uses one captured course projection for due schedule/artifact joining and its
high-water mark. Front/back text is explicitly truncated with a visible
ellipsis at the DTO boundary.

## Notes

- Public-demo mode remains stateless and cannot use the repository mutation
  routes.
- Real-FSRS execution remains covered by the existing optional integration lane
  and is intentionally skipped when the extra is absent locally.
