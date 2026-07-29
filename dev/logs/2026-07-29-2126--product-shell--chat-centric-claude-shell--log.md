# Log: Chat-centric Claude shell

Date: 2026-07-29 21:26 CEST
Area: product-shell

## Summary

Connected the Cardine product shell to a faithful Claude-style chat workspace
while preserving the harness's existing same-origin API and trust boundaries.
The sidebar now supports compact, expanded, and mobile-sheet states. The
composer has an IME-safe Enter contract, the conversation has explicit learner
and tutor boundaries, and every visible study destination has a tested,
truthful state.

Hosting was evaluated but not implemented because the user deferred it.
Owner-only ingress remains mandatory before production deployment.

## Files Changed

- `src/study_agent/demo/browser.html`: Claude-style rail and semantic shell.
- `src/study_agent/demo/browser.css`: compact/expanded/mobile navigation,
  chat-first layout, message boundaries, composer, responsive layering.
- `src/study_agent/demo/browser.js`: keyboard contract, busy-state guards,
  navigation state, truthful current-session link, mobile focus/ARIA behavior.
- `src/study_agent/demo/browser.py`: allowlisted packaged icon serving and
  correct `UiRequestError` status propagation.
- `src/study_agent/demo/icons/`: ten Phosphor icons and MIT license.
- `pyproject.toml`: packaged icon data.
- `tests/e2e/test_cardine_browser_contract.py`: real HTTP and keyboard
  contracts, including every referenced icon.
- `tests/unit/demo/test_browser.py`: icon allowlist/traversal coverage.
- `tests/unit/demo/test_browser_assets.py`: chat, sidebar, and IME contracts.
- `design-qa.md`: visual evidence and final design verdict.

## Verification

- Focused browser suite: 22 passed.
- JavaScript syntax: passed.
- Ruff across `src` and `tests`: passed.
- In-app-browser desktop/mobile flow audit: passed; no console errors.
- Fresh visual critique: no remaining P0/P1/P2 defects.
- Regenerated `dist/study_agent_harness-0.2.0-py3-none-any.whl`, installed it
  into a clean target, and loaded all ten icons through the packaged
  `BrowserSurface`, including 10/10 over HTTP: passed.
- Mypy: unavailable because the module is not installed.
- Full pytest was interrupted after 767 passes because the run had collected
  an E2E assertion before its final edit; the affected current suite was then
  recollected and passed.

## Notes

- The public demo's English tutor fixture can still appear beside an Italian
  learner prompt. This is fixture content, not UI-owned generation; production
  language belongs to the configured tutor service.
- The current rail action is deliberately **Nuova domanda**. Rename it to
  **Nuova chat** only when the backend exposes canonical session creation.
- Recommended private hosting when resumed: Fly private networking plus
  WireGuard for strongest isolation; Vercel deployment protection is the
  browser-only alternative on a plan that protects production deployments.
