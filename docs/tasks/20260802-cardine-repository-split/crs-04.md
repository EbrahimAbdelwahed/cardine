# Task Bead: crs-04 Pin migration and packaging regression coverage

Status: Open
Priority: P1
Type: task
Depends On: cardine-ui-fix-crs-03-4mw
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

Focused tests prove independent repository identity, package-data, commands, browser journeys, replay, auth, and container configuration before the full gate.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- Cardine focused and clean-package behavior is regression-tested.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- No ADR/glossary change: tests pin approved behavior

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

Metadata and packaging changes can silently break commands, bundled assets, and private browser flows even when source tests pass.

## What To Do

- Add only missing behavior-oriented migration and packaging tests.
- Exercise repository-backed browser, restart/replay, auth/CSRF, and command/package-data smoke paths.
- Keep network and credentials opt-in.

## Likely Files / Packages

- tests/: focused migration and packaging regressions
- .github/workflows/ci.yml: only if required for test execution

## Acceptance Criteria

- [ ] Tests fail on stale product identity or missing package assets.
- [ ] Default tests are offline and credential-free.
- [ ] No implementation-detail-only assertions are introduced.

## Verification

- `python -m pytest tests/e2e/test_cardine_private_product_journey.py tests/e2e/test_cardine_repository_browser_journey.py tests/unit/demo/test_private_production_packaging.py`: expected to pass or produce documented output
- `node --check src/study_agent/demo/browser.js`: expected to pass or produce documented output

## Out Of Scope

- Changing production behavior beyond approved normalization.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
