# Plan: Loopback runtime API key settings

Date: 2026-08-14 23:40
Area: cardine

## Goal

Keep the standard local repository preview password-free while allowing the owner
to enter `OPENAI_API_KEY` from the Settings UI. The key must be immediately visible
to new repository/model adapters and remain runtime-only.

## Scope

- In scope:
  - Compose one shared `RuntimeCredentialStore` for the local CLI path.
  - Expose runtime model settings in `local_repository` mode without activating auth.
  - Restrict unauthenticated local settings mutations to a loopback bind and exact same-origin requests.
  - Render local settings without private-account/logout copy.
  - Preserve private and local-owner-setup behavior.
  - Restart and verify the supervised live server.
- Out of scope:
  - Persisting the API key.
  - Removing the legacy setup flow.
  - Non-loopback unauthenticated settings.

## Approach

1. Generalize the settings wrapper so it can preserve either `private` or `local_repository` mode while retaining the existing private compatibility surface.
2. In the CLI local path, create one credential store and pass the same instance to both `RepositoryUiApplication(environment=...)` and the settings wrapper.
3. Permit local settings only on loopback and require exact same-origin validation for their POST routes.
4. Make the Settings page copy and controls mode-aware; keep the secret input write-only and cleared after use.
5. Add focused unit/browser/HTTP tests for shared-store visibility, no auth gate, origin rejection, no secret echo, and private-mode compatibility.
6. Run focused tests, Ruff, JavaScript syntax validation, diff checks, independent semantic/security review, then restart the launchd job.

## Risks

- Separate credential-store instances would make the UI report success while model adapters still see no key.
- An unrestricted localhost POST surface could permit cross-site requests to mutate runtime configuration.
- Reusing private-mode presentation unchanged could reactivate login or expose misleading account/logout controls.
- A secret must never enter responses, diagnostics, logs, SQLite, or browser storage.

## Verification

- Focused `pytest` for product settings, browser surface/assets, and authenticated/private journey compatibility.
- HTTP test: local Settings GET works; credential POST requires exact local Origin and never echoes the secret.
- `ruff check` on changed Python files.
- `node --check src/cardine/demo/browser.js`.
- `git diff --check`.
- Live health reports `mode: local_repository`; UI accepts a runtime key without password.
