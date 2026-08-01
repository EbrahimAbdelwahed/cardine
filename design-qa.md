# Design QA: Cardine AI-native primitives

Date: 2026-07-31 04:15 CEST
Target: adapt the complete useful component vocabulary from Beautiful UI to
Cardine's Claude-like, chat-centric medical-study workspace without copying the
reference site's shell or inventing backend capabilities.

## Evidence

- Reference captures and exposed-code inventory:
  `dev/plans/assets/beautiful-ui-pattern-audit/`
- Cardine desktop states:
  `20-cardine-ai-home-refined.jpg`, `21-cardine-ai-chat-refined.jpg`,
  `24-cardine-command-search-final.jpg`, `25-cardine-fonti.jpg`, and
  `26-cardine-fonti-filtered.jpg`
- Cardine mobile states:
  `27-cardine-mobile-home.jpg`, `28-cardine-mobile-drawer.jpg`, and
  `29-cardine-mobile-search.jpg`
- Reference and implementation in the same comparison inputs:
  `30-reference-cardine-chat-comparison.jpg` and
  `31-reference-cardine-search-comparison.jpg`

All paths above are under
`dev/plans/assets/beautiful-ui-pattern-audit/`.

## Pattern coverage

All 17 reference patterns are mapped: loading, bounded thinking/activity,
staged answer reveal, approval, tool status, task rows, compact chat,
recommendation, context cards, diff table, records table, filter table,
sidebar search, command search, insight deck, source/code excerpt, and
fine-tuning/follow-up controls.

The patterns use Cardine's existing typography, dark/light tokens, compact and
expanded rail, learner-message boundaries, unboxed tutor prose, bottom
composer, route DTOs, and canonical commands. Tool traces and transcripts that
are absent from a DTO are identified as unavailable; they are not fabricated.

## Interaction and accessibility

- Enter sends and Shift+Enter inserts a newline; IME composition is preserved.
- Command search supports `/`, Cmd/Ctrl+O, filtering, arrows, Enter, and Escape.
- Fonti search and filters are operable by pointer and keyboard.
- Follow-up and fine-tune controls target the correct composer and do not
  mutate hidden model settings.
- The answer reveal keeps the complete canonical response in the DOM, respects
  reduced motion, finishes in 280 ms, and announces the completed response
  once through an atomic live status.
- Mobile drawer focus/inert state recovers when returning to desktop.
- The browser console is clean on the final local preview.

## Comparison result

The combined chat and search comparisons preserve the reference's compact
density, restrained hierarchy, low-noise surfaces, and short interaction
motion while remaining recognizably Cardine. Desktop, compact rail, mobile,
loading, empty, success, and error states have no remaining P0, P1, or P2
visual or interaction differences within the product-specific adaptation.

Two independent review passes were completed. The final semantic and
accessibility re-review reported zero P0/P1/P2 findings.

## Verification

- `node --check src/study_agent/demo/ai-primitives.js` and
  `node --check src/study_agent/demo/browser.js`: passed.
- UI boundary suite: 101 passed.
- Full repository suite: 2067 passed, 13 optional/live tests skipped.
- `python -m ruff check src tests`: passed.
- `git diff --check`: passed.
- Wheel build: passed; the wheel contains both primitive assets and the
  Phosphor search icon.
- Clean wheel install: `BrowserSurface` loaded the JavaScript, CSS, and icon
  assets successfully.
- Final in-app browser reload at `http://127.0.0.1:8765/`: correct home state
  and zero console messages.

Final result: passed.

---

# Design QA: private continuation dock, Login, and Settings

Date: 2026-07-31 13:40 CEST

## Evidence

- Reference: `dev/plans/assets/continuation-dock-reference.png`
- Final: `dev/plans/assets/private-shell-dock-final-anchored.png`
- Same-input comparison:
  `dev/plans/assets/private-shell-dock-comparison.png`
- Login, Settings, expanded rail, and mobile captures:
  `dev/plans/assets/private-shell-login.png`,
  `dev/plans/assets/private-shell-settings.png`,
  `dev/plans/assets/private-shell-sidebar-expanded.png`, and
  `dev/plans/assets/private-shell-mobile.png`

## Result

The final dock is a 111 px two-layer component with a 68 px single-row
composer. It occupies the final grid row, sits 12 px from the viewport bottom,
and leaves the independently scrolling route view above it. Empty pages cannot
pull it upward. It remains unique in the DOM, appears only on the seven
approved non-chat routes, and has no horizontal overflow at 320/390 px.

Login and Settings retain Cardine's existing type, spacing, focus, rail, and
surface language. The API-key input is write-only, cleared after submission,
and not presented as a password-manager credential.

Final result: passed.
