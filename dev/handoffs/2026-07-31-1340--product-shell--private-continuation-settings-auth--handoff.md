# Handoff: private continuation, settings, and auth

Date: 2026-07-31 13:40
Area: product-shell

## Current State

The requested UI and private boundary are implemented. The continuation bar is
thin and anchored to the bottom grid row; Login and Settings are functional;
targeted unit, integration, security, semantic, and visual checks pass.

## Completed

- Seven-route dock allowlist; hidden on Oggi, Chat, Login, and Settings.
- Bottom anchoring independent of route content height.
- Login/logout/expiry, CSRF, exact Origin/Host, secure derived cookies, HSTS,
  and bounded rate limiting.
- Runtime-only write-only API-key replace/remove for the Luna adapter.
- Draft persistence, Enter/Shift+Enter/IME, success-only clear, and stale retry
  after canonical sequence refresh.
- Public-demo isolation and responsive Login/Settings/account shell.
- Seven `jakubkrehel/skills` installed and used through independent UI audits.

## Remaining

- Add one private repository-backed E2E fixture that forces a real pending
  continuation and a genuine 409, then removes the conditional assertion in
  `tests/e2e/test_cardine_private_product_journey.py`.
- Run the entire repository suite and wheel/clean-install inspection.
- Configure ingress-side per-real-client login throttling before remote use.

## Important Context

- `study-agent-shell-web --private` requires repository, course, and session
  selection plus `CARDINE_OWNER_PASSWORD_HASH` and `CARDINE_PUBLIC_ORIGIN`.
- HTTPS origins automatically use the Secure `__Host-` cookie.
- A process-local visual preview remains open at `http://127.0.0.1:8765/`.

## Verification

- Focused unit checks: 49 passed.
- Private browser/transport E2E: 8 passed.
- Ruff, Node syntax, and diff checks: passed.
- Final visual measurement: dock 111 px; composer 68 px; 12 px bottom gap.
