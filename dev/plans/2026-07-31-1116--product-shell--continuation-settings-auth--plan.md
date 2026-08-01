# Plan: Continuation dock, settings, and personal access

Date: 2026-07-31 11:16
Area: product shell and private access

## Goal

Extend Cardine's chat-centric workspace so every non-chat study route keeps a
compact, screenshot-faithful path back into the tutor. Add a coherent Settings
surface for account, personal data, and model configuration, plus the smallest
secure login boundary suitable for the product's current personal/private
deployment stage.

## Scope

- In scope:
  - A persistent compact continuation dock on Fonti, Proposte, Verifiche,
    Evidenze, Ripasso, Piano, and Conflitti.
  - A bounded "Ultimo turno" affordance that opens the current chat without
    duplicating or fabricating conversation state.
  - Model label, tutor trust state, add-source shortcut, keyboard-safe composer,
    responsive mobile layout, and light/dark parity.
  - Settings navigation and UI for account identity, local data/repository
    information, privacy, model status, API-key entry/replacement/removal, and
    sign-out.
  - A private single-user login and session boundary appropriate to the current
    stdlib HTTP server, with CSRF protection and no secret returned to the
    browser.
  - Versioned API contracts, tests, packaging, browser E2E, security review,
    and screenshot comparison against the supplied continuation-bar reference.
  - One read-only audit agent for each installed `jakubkrehel/skills` skill,
    consolidated into implementation scope before final QA.
- Out of scope:
  - Multi-tenant organizations, billing, social/OAuth providers, password
    recovery, email delivery, or production hosting.
  - Persisting a plaintext provider key in browser storage, repository events,
    logs, snapshots, or source-controlled files.
  - Changing domain authority or allowing settings UI to mutate course state.

## Working Product Contract

1. The continuation dock is application chrome, not part of each route DTO.
   It reads the current session summary and submits through the existing
   canonical `/api/v1/session/turns` command.
2. Login/settings are transport/product-shell concerns. They do not enter the
   study-domain event log.
3. API-key material is write-only. The UI receives only configured/provider
   metadata. Exact persistence and session contracts require the architecture
   and security boundary reviews before implementation.
4. Public-demo mode remains stateless, read-only, and never exposes login,
   account, or credential mutation endpoints.
5. The compact dock does not appear on Login, Settings, Oggi, or the full Chat
   route; those surfaces already own a primary action or security context.

## Approach

1. Inventory and install all seven interface skills; run one bounded read-only
   agent per skill in controlled waves.
2. Approve the auth/session/API-key boundary with a read-only architect before
   adding public contracts.
3. Add failing unit, transport, and real-browser tests for the continuation
   dock, settings routes, login/session/CSRF behavior, secret redaction, mobile
   reflow, keyboard/IME handling, and public-demo isolation.
4. Implement transport-owned access/config modules and versioned UI endpoints
   without changing domain ownership.
5. Implement the screenshot-faithful compact dock, Login, Settings, account
   menu, and navigation using existing Cardine tokens and Phosphor assets.
6. Run a dedicated security review, semantic regression review, and approved
   fixes.
7. Verify focused/full tests, Ruff, package/install, real-browser E2E, console,
   desktop/mobile/accessibility states, and same-input visual comparison.
8. Record the accepted architecture decision, completion log, bead status, and
   `design-qa.md`.

## Risks

- Custom authentication can create a false sense of production security; the
  boundary must remain explicitly single-user and fail closed.
- Provider credentials can leak through errors, logs, DTOs, form repopulation,
  browser storage, or repository serialization.
- A global dock can cover route actions or mobile content; layout must reserve
  safe space and reflow at 320 px / 200% zoom.
- Reusing the session-turn command from non-chat routes can race the current
  high-water sequence; stale-state recovery must remain explicit.
- The previous OSS-only ADR excluded hosted auth; this product-shell exception
  must be documented without weakening the reusable harness core.

## Verification

- Narrow unit tests for access/config contracts and UI asset markers.
- Transport tests for unauthenticated, authenticated, CSRF, redaction, logout,
  and public-demo behavior.
- Existing UI/repository E2E plus new non-chat dock and settings/login journeys.
- `python -m pytest -q`, `python -m ruff check src tests`, `git diff --check`.
- Wheel asset inspection and clean install.
- In-app browser: keyboard, IME, focus restoration, 320/390/mobile and desktop,
  dark/light, loading/error/success, and zero console errors.
- Reference and implementation combined in one comparison input; iterate until
  `design-qa.md` says `final result: passed`.
