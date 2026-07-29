# Plan: Chat-centric E2E, Claude benchmark, and hosting decision

Date: 2026-07-29 17:00
Area: product-shell
Status: Completed

## Goal

Turn Cardine into a cognitively light, chat-centric, personal study workspace
that can be used for long sessions. Audit every exposed user flow, fix keyboard
submission, establish explicit learner/tutor message boundaries, and select a
private production hosting topology appropriate for the existing Python
container. A faithful Claude chat UI recreation is a product requirement;
Cardine keeps its own name and medical-study content.

## Scope

- In scope:
  - Evidence-based comparison of container hosting options.
  - Owner-only access enforced before the application receives a request.
  - Current-flow screenshots and end-to-end browser checks.
  - Faithful Claude information hierarchy, spacing, conversation width,
    composer, sidebar, and chat behavior, adapted to Cardine's content.
  - Enter to send, Shift+Enter for a newline, IME-safe submission, loading and
    retry behavior, textarea growth, and focus restoration.
  - Chat-first session layout with secondary study tools available without
    competing with the conversation.
  - Automated browser-level tests for navigation, session submission, optional
    feature states, and responsive behavior.
- Out of scope:
  - Remote deployment before a host and owner-only access method are selected.
  - Copying Claude trademarks, text, icons, fonts, or other proprietary assets.
  - Claiming repository-backed flows that are still intentionally unavailable
    in the sanitized public demo.
  - Authentication, tenancy, or public repository mutation.

## Work Packages

1. Hosting research: compare current official support for OCI containers,
   TLS/ingress controls, health checks, scaling, persistent storage, pricing,
   and operational burden. Recommend one target for the stateless demo and one
   later topology for repository-backed mode.
2. Reference and current-product audit: capture Claude and Cardine where
   accessible, enumerate every visible flow, and record evidence-backed UX,
   accessibility, and cognitive-load findings. If authenticated Claude cannot
   be captured, use official screenshots for the accessible states and name the
   missing evidence instead of guessing.
3. E2E contract: add independent browser-level coverage for navigation,
   keyboard composition, submit/retry, unavailable states, and mobile shell.
4. Chat redesign: implement the selected Claude-inspired composition in the
   existing dependency-free assets, preserving API and trust boundaries.
5. Verification: run focused tests, full relevant Python/JS checks, desktop and
   mobile browser flows, console inspection, screenshot comparison, independent
   semantic review, and record results.

## Invariants

- The same-origin v1 API and canonical service owners remain unchanged unless
  a separately approved backend contract requires change.
- Enter sends only when composition is complete; Shift+Enter inserts a newline.
- Empty or busy composers do not submit.
- The learner and tutor are visually distinct without relying on color alone.
- The primary viewport prioritizes conversation and composition; supporting
  study tools remain reachable but visually secondary.
- Public demo remains stateless, sanitized, and non-secret.
- Production access is owner-only; an unprotected public URL is not acceptable.
- No UI component calls a model provider directly.

## Risks

- Claude may require authentication, limiting direct capture of some states.
- A faithful visual reference can become an inappropriate branded clone;
  Cardine identity and content must remain distinct even while layout and
  interaction are matched.
- Browser automation tooling may not support every responsive viewport; any
  unverified state must be named.
- A platform that supports containers may still require an external ingress to
  enforce total request deadlines and rate limits.

## Verification

- `node --check src/study_agent/demo/browser.js`
- Focused UI asset and browser tests.
- Automated browser E2E at desktop and mobile viewport sizes.
- `python -m pytest -q`
- `python -m ruff check src tests`
- `mypy src tests`
- Fresh wheel/package smoke.
- Screenshot-backed UX audit and design QA with no open P0/P1/P2 findings.

## Outcome

- Hosting was evaluated but intentionally deferred. Owner-only production
  remains a release gate; an unprotected public URL is rejected.
- The Claude reference was captured in the authenticated in-app browser and
  compared at a matching viewport with Cardine.
- The shell now supports a 52 px compact rail, a 288 px expanded rail, and a
  modal mobile drawer.
- Enter, Shift+Enter, IME composition, busy-state submission, every visible
  route, unavailable states, stale-sequence handling, and packaged assets are
  covered by focused tests.
- Final visual critique reported no remaining P0, P1, or P2 defects.
