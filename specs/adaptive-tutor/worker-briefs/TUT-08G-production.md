# Worker Brief: TUT-08G full-product E2E closure

Date: 2026-07-30
Area: adaptive tutor / Cardine product shell
Bead: `TUT-08G-full-product-e2e-closure.md`
Profile: `cardine-product-slice`

## Goal

Validate every completed Cardine product journey through real HTTP and a real
browser, close semantic/security/UI findings, and produce release evidence.

## Scope

- Journey tests and fixtures under `tests/e2e` and `tests/integration/demo/TUT08`.
- Narrow accessibility/testability fixes in the Cardine browser assets.
- TUT-08G bead/log and screenshot/console/network evidence.
- No hosting/auth implementation; verify only that current private/local and
  public-demo boundaries do not expose repository mutations or credentials.

## Required Journeys

1. Bootstrap, Today, Piano, readiness, missing/conflicted dates.
2. Direct chat, grounded response, reload/process restart.
3. Suspension, reload, exact continuation resume.
4. Materials and bounded provenance.
5. Artifact proposal/list/accept/reject/retry/stale/reload.
6. Closed/free assessment attempt then grade then evidence.
7. Flashcard acceptance, separate enrollment, due/reveal/rating/reload.
8. Context conflict `StatementId` resolution and stale/cross-scope rejection.
9. Public-demo immutability and credential/path/source-text redaction.

## Required Cross-Cutting States

- Success, valid empty, unavailable optional owner, malformed/oversize, error,
  stale, race, exact retry, reload, and process restart.
- Keyboard-only operation, focus restoration, live status, dialog Escape/focus
  trap where present, desktop/mobile overflow, reduced motion.
- Zero console errors/unhandled rejections and zero unexpected failed requests.
- No duplicate event/provider/model/scheduler work under retry.

## Real-Browser Rule

Use the already available in-app/internal browser automation seam when it can
produce reproducible screenshots and console/network evidence. Do not add a
production dependency. A test-only Playwright dependency requires orchestrator
approval and a recorded installation/CI contract; static Node/HTTP assertions
alone do not satisfy the bead.

## Definition of Done

- A–F complete and zero unresolved P0/P1/P2 review findings.
- Focused and full pytest, Ruff, mypy when available, build/wheel smoke, and
  `git diff --check` green or every pre-existing failure documented.
- Base/no-FSRS and optional real-FSRS lanes are honest.
- Independent semantic, security, and UI reviews are resolved.
- Exact commands, screenshots, console/network evidence, and remaining
  explicitly out-of-scope hosting/auth work are logged.

## Coordination

Do not begin until A–F owners are stable. You are not alone in the codebase;
preserve all prior behavior, do not recursively delegate, and do not widen
scope into hosting or public access.
