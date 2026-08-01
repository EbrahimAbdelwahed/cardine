# Log: public auth probe 404

Date: 2026-08-01 10:15
Area: product-shell

## Summary

Fixed the browser console `404` caused by probing `/api/v1/auth/session` on
the sanitized public demo. That namespace is intentionally absent in public
mode; the browser now reads `/health` first and only requests the auth session
when the server reports `private` mode.

## Files Changed

- `src/study_agent/demo/browser.js`: add health-mode discovery before the
  private auth-session request.

## Verification

- `node --check src/study_agent/demo/browser.js`: passed.
- `python -m pytest -q tests/unit/demo/test_browser_assets.py tests/integration/demo/TUT08/test_browser_surface.py tests/e2e/test_cardine_repository_browser_journey.py::test_public_demo_browser_is_stateless_read_only_and_redacted`: 14 passed.
- `python -m pytest -q tests/e2e/test_cardine_private_product_journey.py tests/unit/demo/test_private_access.py tests/unit/demo/test_product_settings.py`: 45 passed.
- `git diff --check`: passed.

## Deployment Assessment

The public surface is suitable for a guarded evaluation only through the
existing stateless `--public-demo` container. It must be deployed behind TLS,
an ingress with body/time limits and request-rate controls, and access logs
that omit request bodies. The repository-backed private mode, login, settings,
and credentials must not be exposed on a public bind. No deployment was
performed in this task.
