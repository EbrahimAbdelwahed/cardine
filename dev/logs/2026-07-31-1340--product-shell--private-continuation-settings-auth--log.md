# Log: private continuation, settings, and auth

Date: 2026-07-31 13:40
Area: product-shell

## Summary

Implemented the global non-chat continuation dock, private single-owner Login,
account/settings surfaces, runtime-only GPT-5.6 Luna credential management, and
the private HTTP security boundary. The final dock was tightened and assigned
to the last viewport grid row so empty pages cannot lift it.

## Files Changed

- `src/study_agent/demo/browser.html`: global dock and account surfaces.
- `src/study_agent/demo/browser.css`: compact anchored dock and responsive UI.
- `src/study_agent/demo/browser.js`: auth/settings, draft/IME, stale refresh,
  and session-expiry recovery.
- `src/study_agent/demo/private_access.py`: private access boundary.
- `src/study_agent/demo/product_settings.py`: safe settings and credential
  overlay.
- `src/study_agent/demo/browser.py`: private transport and CLI boundary.
- `tests/unit/demo/test_private_access.py`: access regression coverage.
- `tests/unit/demo/test_product_settings.py`: settings regression coverage.
- `tests/e2e/test_cardine_private_product_journey.py`: private browser journey.

## Verification

- Node syntax, focused Ruff, and `git diff --check`: passed.
- Private access/settings/browser asset unit tests: 49 passed.
- Private browser plus browser transport E2E: 8 passed.
- In-app browser: dock 111 px, composer 68 px, 12 px bottom gap, and route
  viewport ending exactly at the dock.
- Independent security findings on cookie policy, HSTS, isolated credentials,
  and browser persistence: fixed.
- Independent semantic findings on expiry recovery, stale refresh, and private
  CLI repository selection: fixed.

## Notes

- Remote ingress must enforce per-real-client login throttling; the loopback app
  does not trust arbitrary forwarded-IP headers.
- Remaining final-hardening: add a private repository-backed E2E fixture for a
  real pending continuation and genuine 409 retry, then run full package QA.
