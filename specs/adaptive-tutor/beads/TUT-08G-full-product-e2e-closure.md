# Task Bead: TUT-08G full-product E2E and release closure

Status: Complete
Priority: P0
Type: closure
Depends On: TUT-08B, TUT-08C, TUT-08D, TUT-08E, TUT-08F

## Outcome

Every visible Cardine control is exercised in a real browser against a
temporary restart-safe repository populated only through canonical
services/verified headless paths, and the complete product passes release
gates with no open P0/P1/P2 findings.

## Slice Strategy

contract

Fresh Context Fit: yes

## Spec Coverage

- FP-10 complete E2E matrix.
- FP-11 public-demo isolation.
- Closure evidence for FP-01 through FP-09.

## Grilling Evidence

- Session/artifact:
  - `specs/adaptive-tutor/cardine-full-product.md`
  - TUT-08A through TUT-08F verification logs
- Decision state: complete; all dependencies and release gates passed
- ADR/glossary changes: none

## Worker Profile

reuse `cardine-product-slice`

Rationale:

Closure reuses the same product boundaries but is independently owned by a test
engineer and semantic reviewer.

## Context

Static asset and HTTP contract tests cannot prove real focus, keyboard,
responsive, continuation, provider, and restart behavior. A real-browser
journey is required.

## What To Do

- Add or justify one test-only real-browser dependency if the existing in-app
  browser seam cannot run repeatably in CI.
- Populate a temporary repository only through real services, verified
  capability outputs, and explicit scheduler configuration.
- Automate navigation/reload, new chat, suspension/response, provenance,
  artifact accept/reject, closed/free assessment, grade, evidence refresh,
  recall reveal/all ratings, context resolution, Today/Piano, stale/exact retry,
  and process restart.
- Cover desktop/mobile, keyboard, focus, accessibility, reduced motion,
  overflow, and console/network errors.
- Run full pytest, Ruff, strict mypy, wheel/install/package-assets, base/no-FSRS,
  optional real-FSRS, replay, architecture, and public-demo isolation gates.
- Run independent semantic, security, and UI reviews; fix approved findings.
- Close child beads and TUT-08 with exact commands/evidence.

## Likely Files / Packages

- `tests/e2e/`
- focused browser fixtures/config
- dev logs/handoffs and TUT bead status documents
- only approved fixes in production packages

## Acceptance Criteria

- [x] Every visible control has a successful real-browser path or an explicitly
  approved honest unavailable state.
- [x] Restart/reload retains every canonical lifecycle.
- [x] Exact retry and stale conflicts are proven for every mutation family.
- [x] No browser console errors, unhandled network failures, focus traps,
  desktop/mobile overflow, or keyboard regressions remain.
- [x] Public demo cannot reach repository state, provider calls, or credentials.
- [x] Full release matrix passes and no open P0/P1/P2 review finding remains.

## Verification

- complete commands recorded in the closure log
- real-browser E2E artifact/screenshots
- full suite, Ruff, strict mypy, wheel/install, replay, optional dependency, and
  architecture gates

## Out Of Scope

- Remote hosting, authentication, tenancy, or public repository mutation.

## Notes / Handoff

- Do not seed projection rows or forge events to make UI tests pass.
- Closure evidence is recorded in
  `dev/logs/2026-07-30-2359--adaptive-tutor--tut08g-full-product-e2e--log.md`.
