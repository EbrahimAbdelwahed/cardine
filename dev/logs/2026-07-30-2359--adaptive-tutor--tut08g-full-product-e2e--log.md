# Log: TUT-08G full-product E2E and release closure

Date: 2026-07-30 23:59
Area: adaptive-tutor

## Summary

Completed the local Cardine product composition. Every visible route and
command is connected to the repository-backed Study Agent harness or presents
an explicit honest unavailable state. Canonical chat, artifacts, assessments,
evidence, recall, context resolution, Today, Piano, materials, provenance,
reload, restart, stale-command, and exact-retry behavior are covered.

The final hardening pass added durable terminal conversation receipts,
single-HWM post-mutation DTO capture, process-wide per-repository mutation
serialization, recall session preflight, Host validation, decoded opaque route
segments, navigation race guards, preserved disabled states, complete light
and dark theme tokens, and canonical sidebar-count refresh after every
mutation.

## Files Changed

- `src/study_agent/application/conversation_turn.py`: durable adaptive-turn
  orchestration and terminal retry receipts.
- `src/study_agent/demo/ui_application.py`: repository-backed product routes,
  canonical mutation DTOs, sequencing, idempotency, and recall/assessment
  flows.
- `src/study_agent/demo/browser.py`: hardened localhost/public-demo transport.
- `src/study_agent/demo/browser.html`, `browser.css`, `browser.js`: complete
  chat-centric Cardine surface and interaction states.
- `tests/e2e/test_cardine_repository_browser_journey.py`: real Chrome/CDP
  product journeys.
- `tests/integration/demo/TUT08/`: complete lifecycle, restart, concurrency,
  public-demo, HTTP, and browser-contract coverage.
- `specs/adaptive-tutor/`: full product contract and beads TUT-08A through G.

## Verification

- `python -m pytest -q`: **2047 passed, 12 skipped in 42.07s**.
- `python -m pytest -q tests/integration/demo/TUT08 tests/e2e`:
  **52 passed in 14.97s**.
- Focused semantic/concurrency regression matrix: **41 passed**.
- Focused HTTP and real-Chrome regression matrix: **5 passed**.
- `python -m ruff check .`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.
- `python -m pip wheel . --no-deps --no-build-isolation`: built
  `study_agent_harness-0.2.0-py3-none-any.whl`, SHA-256
  `f63e11c700ab912083c35876f601e2ff335c94d8dc35d3d577b7d1ba5a35b250`.
- Clean virtualenv wheel install: passed; `study-agent-shell-web --help`
  passed and packaged HTML/CSS/JS assets were verified.
- Internal browser: desktop and 390x844 mobile traversal, collapsed/expanded
  sidebar, Enter submission, reload, assessment present/attempt/grade/evidence,
  recall accept/enroll/reveal/rating, context resolution, route traversal,
  focus, no horizontal overflow, and zero console errors were verified.
- Independent final reviews: semantic **P0=0/P1=0/P2=0**; security
  **P0=0/P1=0/P2=0**; UI **P0=0/P1=0/P2=0**.

## Notes

- The 12 skips are explicit optional/environment gates: opt-in network model
  smoke tests, optional FSRS integration, and the platform-contained PDF
  workaround. The base/no-FSRS behavior is tested and honest.
- Strict mypy was not run because mypy is not installed in this environment.
- A configured live DeepSeek synthetic smoke had already passed in the TUT-08B
  closure without logging credentials.
- Hosting, authentication, tenancy, README submission packaging, and the
  sub-three-minute launch video remain intentionally out of this local product
  bead.
- Supported mutation serialization is single-process. Cross-process
  repository mutation would require a durable reservation/lease before
  provider or scheduler work.
