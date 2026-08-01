# Log: TUT-08D assessments and learner evidence

Date: 2026-07-30 23:55
Area: adaptive tutor / Cardine product shell

## Summary

Composed the canonical `AssessmentService` into `LocalRepository` with the
existing projection views and `ExactClosedGradingPolicy`. Repository UI reads
are side-effect free: accepted selected-session assessment items appear as
bounded unpresented candidates, while an explicit SERVICE presentation command
creates learner-safe presentations. Separate HUMAN attempt and SERVICE grade
commands map the strict browser response union. Closed grades remain
provider-free; free grades derive a persistent `needs_review` state from the
canonical attempt when no verified owner runtime is truthfully composed.
Contest/supersession and the canonical learner-evidence projection are exposed
with safe grade history, contest dispositions, ratios, references, and exact
 through-sequence. Browser controls include native single/multiple choice inputs
 and a free-response textarea, with terminal action gating. The browser also
 renders a bounded accessible grade lifecycle summary from safe grade-history
 and contest DTO fields, without exposing rubric details.

## Files Changed

- `src/study_agent/cli/repository.py`: canonical assessment service wiring.
- `src/study_agent/demo/ui_application.py`: assessment/evidence DTOs,
  presentation/attempt/grade/contest commands, strict response parsing.
- `src/study_agent/demo/browser.js`: accessible assessment controls and strict
  response command mapping, plus safe lifecycle-history rendering.
- `tests/unit/demo/test_browser_assets.py`: browser asset contract assertion for
  lifecycle-history rendering and hidden-field exclusion.
- `specs/adaptive-tutor/beads/TUT-08D-assessments-and-evidence.md`: acceptance
  criteria marked complete.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/integration/demo/TUT08/test_repository_assessments_evidence.py`: 7 passed (including restart, lifecycle, and external-writer race coverage).
- `PYTHONPATH=src:. python -m pytest -q tests/unit/demo/test_browser_assets.py`: 7 passed.
- `PYTHONPATH=src:. python -m pytest -q tests/unit/assessments tests/contract/assessment tests/integration/test_assessment_ledger_replay.py tests/unit/demo tests/integration/demo/TUT08/test_repository_materials_artifacts_context.py`: 115 passed.
- `PYTHONPATH=src python -m ruff check src/study_agent/cli/repository.py src/study_agent/demo/ui_application.py`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.

## Notes

- Verified free-response recovery remains explicitly unavailable/needs-review;
  no fake grade or provider work is created.
- Existing C2 artifacts/materials/context behavior remains intact.
