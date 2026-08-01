# Task Bead: TUT-08J Private shell continuation, settings, and auth

Status: Implemented; final repository-backed hardening handed off
Priority: P0
Type: product shell, private access, and interaction continuity
Depends On: TUT-08I

## Outcome

Every non-chat study surface retains a compact, screenshot-faithful path back
to the tutor, while Cardine gains a functional private single-owner Login and
Settings experience that never exposes or persists provider secrets in the
browser or study repository.

## Acceptance Criteria

- [x] A stable compact dock appears on Fonti, Proposte, Verifiche, Evidenze,
  Ripasso, Piano, and Conflitti, and is absent on Oggi, Chat, Settings, and
  Login.
- [x] “Ultimo turno” opens the current Chat; the dock submits through the
  canonical session-turn endpoint and preserves draft/request identity on a
  stale retry.
- [x] Pending continuations are never resumed implicitly from the dock.
- [x] Private mode has functional login, logout, expiry, bounded rate limiting,
  exact Origin/Host policy, HttpOnly/SameSite cookies, and CSRF on every
  authenticated mutation.
- [x] Settings exposes safe account/data/model/privacy metadata and supports
  write-only runtime API-key replace/remove for fixed GPT-5.6 Luna.
- [x] Passwords, API keys, session tokens, repository paths, provider payloads,
  and secret-derived identifiers never appear in browser storage, DTOs,
  repository events/config, exports, logs, tracebacks, or `repr`.
- [x] Public demo remains stateless/read-only and returns no auth/settings/
  credential surface.
- [x] Keyboard, IME, focus, 320/390 px, 200% zoom, safe-area, touch-target,
  light/dark contrast, reduced-motion, loading/error/success, and screen-reader
  behavior pass.
- [ ] Full tests, Ruff, package/install, real-browser E2E, security review,
  semantic review, and design QA pass with no P0/P1/P2 findings.

## Evidence

- Visual target:
  `dev/plans/assets/continuation-dock-reference.png`
- Plan:
  `dev/plans/2026-07-31-1116--product-shell--continuation-settings-auth--plan.md`
- Architecture:
  `docs/decisions/ADR-0019--private-product-shell-access-and-runtime-secrets.md`
