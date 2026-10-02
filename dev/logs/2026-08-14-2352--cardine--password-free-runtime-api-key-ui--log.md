# Log: Password-free runtime API key UI

Date: 2026-08-14 23:52
Area: cardine

## Summary

Corrected the local-preview composition so removing owner setup does not remove
the runtime API-key UI. The standard `local_repository` server now starts without
a password while exposing write-only model credential settings. One shared
`RuntimeCredentialStore` backs both the Settings UI and every repository/model
adapter opened after the key is saved.

Local settings mutations require an exact same-origin `Origin` header and remain
limited to the existing loopback-only server boundary. The key stays process-local,
is never echoed, logged, persisted, or placed in browser storage, and clears on
restart.

The supervised live job was restarted without `--local-owner-setup`. Health reports
`mode: local_repository`, and `GET /api/v1/settings` is available without login with
`credential_configured: false`, ready for the owner to enter the key in the UI.

## Files Changed

- `src/cardine/demo/product_settings.py`: generalized runtime settings for private and local repository modes while preserving the private import surface.
- `src/cardine/demo/browser.py`: shared credential-store composition, loopback local settings, and exact same-origin enforcement.
- `src/cardine/demo/browser.js`: local-mode settings copy and controls without account/logout presentation.
- `tests/unit/demo/test_product_settings.py`: local settings and shared-store contract.
- `tests/unit/demo/test_browser.py`: local HTTP origin and secret non-echo contract.
- `tests/unit/demo/test_browser_assets.py`: local settings presentation contract.
- `dev/plans/2026-08-14-2340--cardine--loopback-runtime-api-key-settings--plan.md`: approved implementation boundary.

## Verification

- `.venv/bin/pytest -q tests/unit/demo/test_product_settings.py tests/unit/demo/test_browser.py tests/unit/demo/test_browser_assets.py tests/e2e/test_cardine_private_product_journey.py`: 51 passed, 8 socket-dependent skips in the sandbox.
- Outside the socket sandbox, the three focused local HTTP/store tests: 3 passed.
- Full outside-sandbox browser/private slice: 18 passed, 1 unrelated existing model-readiness fixture failure; the UI correctly reported provider unavailable after the fixture readiness request failed.
- `.venv/bin/ruff check ...`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `git diff --check`: passed.
- Independent semantic review: accepted with no findings.
- Independent security review: approved with no HIGH/MEDIUM findings.
- Live `GET /health`: HTTP 200, `mode: local_repository`, PID 10033.
- Live `GET /api/v1/settings`: HTTP 200, runtime-only privacy DTO, no account/password gate, no secret value.
- Live same-origin empty credential POST: HTTP 400 validation response, proving the route is active without storing a key.

## Notes

- This supersedes the operational limitation recorded in
  `2026-08-14-2330--cardine--remove-local-setup-and-last-turn-failure--log.md`:
  the live server no longer needs an environment-injected key because the local
  Settings UI can populate the shared runtime store directly.
- The earlier sequence-191 failure remains a downstream provider/transport
  `tutor_unavailable` after a successful `propose_flashcards@1` selection; it was
  not an intent-routing failure.
