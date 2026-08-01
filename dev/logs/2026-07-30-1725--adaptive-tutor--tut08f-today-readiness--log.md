# Log: TUT-08F Today and exam readiness

Date: 2026-07-30 17:25
Area: adaptive tutor / Cardine product shell

## Summary

Implemented a projection-only `StudyReadinessView` over one immutable event
capture and an injected UTC clock. Cardine now exposes truthful Today counts
and a source-attributed Piano view for course goals, explicit constraints,
accepted blueprint observations, artifact/evidence facts, and optional recall.
No plan, score, priority, coverage, retention, or mastery value is persisted or
invented.

Repository reads now bind readiness, tutor snapshot, presentations, artifacts,
assessments, evidence, recall, and study context to the same event high-water
mark. A bounded retry returns a conflict rather than mixing versions under an
external write.

## Files Changed

- `src/study_agent/application/study_readiness.py`: immutable attributable view.
- `src/study_agent/application/__init__.py`: public application exports.
- `src/study_agent/cli/repository.py`: repository composition.
- `src/study_agent/demo/ui_application.py`: consistent bootstrap/plan/session DTOs.
- `src/study_agent/demo/browser.js`: Today aggregation and attributed Piano UI.
- `src/study_agent/demo/browser.css`: responsive Today/source presentation.
- `tests/unit/application/test_study_readiness.py`: deterministic projection tests.
- `tests/integration/demo/TUT08/test_repository_readiness_plan.py`: product integration.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/application/test_study_readiness.py tests/integration/demo/TUT08/test_repository_readiness_plan.py tests/unit/cli/test_repository.py tests/unit/demo/test_browser_assets.py`: 40 passed.
- Focused readiness/repository/UI suite run by the worker: 62 passed.
- `PYTHONPATH=src:. python -m ruff check ...`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.
- Independent semantic re-review: zero remaining P0/P1/P2 findings.
- `python -m mypy ...`: unavailable because mypy is not installed.

## Notes

- Loopback HTTP/browser checks are part of TUT-08G. The socket-enabled focused
  run already passed 10/10 outside the restricted sandbox.
