# Plan: Claude polish parity

Date: 2026-07-29 21:45
Area: product-shell
Status: Completed

## Goal

Close the remaining visible and interaction-quality gap between Claude's
authenticated chat UI and Cardine. Fidelity includes typography, iconography,
spacing, component geometry, hover/press/focus states, sidebar transitions,
composer behavior, tooltips, drawers, and reduced-motion behavior.

## Scope

- In scope:
  - Fresh equivalent-state screenshots of Claude and Cardine.
  - Measured font, color, geometry, border, shadow, and spacing comparison.
  - Desktop compact/expanded rail, home composer, conversation, and mobile
    drawer.
  - Hover, active, focus-visible, tooltip, rail, composer, and drawer motion.
  - Iterative screenshot and interaction QA until no P0/P1/P2 difference
    remains in the audited states.
- Out of scope:
  - Claude trademarks or proprietary assets.
  - Backend session creation not owned by the current harness.
  - Hosting and authentication.

## Approach

1. Capture fresh Claude and Cardine states at the same viewport and inspect
   computed styles and motion timings.
2. Produce side-by-side images, focused crops, and visual metrics.
3. Audit the current CSS/JS motion contract against the Emil/review-animations
   standards.
4. Implement one coherent visual-system pass, preserving existing API and
   accessibility contracts.
5. Re-capture, compare, run an unprimed visual critique, fix every remaining
   P0/P1/P2 issue, and repeat as necessary.
6. Run focused UI/E2E/package checks and an independent semantic review.

## Risks

- Claude's authenticated UI may change during the audit.
- Anthropic's proprietary fonts are unavailable; the closest legal system
  serif/sans fallbacks must be judged by visible metrics.
- Exact pixel equality is neither possible nor desirable where Cardine exposes
  different content, but geometry, hierarchy, interaction behavior, and craft
  must be visibly equivalent.

## Verification

- Fresh same-viewport Claude/Cardine screenshots and crops.
- Browser checks for hover, press, focus, compact/expanded, tooltip, drawer,
  Enter, Shift+Enter, and reduced motion.
- Focused pytest, JavaScript syntax, Ruff, wheel asset smoke.
- Fresh screenshot critique with no open P0/P1/P2 findings.
- Motion review verdict: Approve.
