# Log: Claude polish parity

Date: 2026-07-29 22:15
Area: product-shell

## Summary

Audited authenticated Claude and Cardine at equivalent 1280×720 expanded and
collapsed states, then aligned the shell geometry, theme, type hierarchy,
composer, rail density, icon weight, and interaction feedback. Cardine now
tracks the system light/dark preference, keeps the 288 px expanded and 48 px
compact rails, and matches Claude's 672×125 px composer placement.

The composer now exposes honest working controls: the plus opens Fonti, the
tutor control opens status/provenance, the send affordance appears only for
non-empty input, Enter submits, and Shift+Enter inserts a newline. Desktop
compact tooltips, press feedback, focus states, reduced motion, and a 220 ms
mobile drawer were verified. Phosphor arrow, caret, and book icons remain
allowlisted and packaged with their license.

## Files Changed

- `src/study_agent/demo/browser.css`: Claude-caliber layout, theme, motion,
  responsive rail, composer, and contrast tokens.
- `src/study_agent/demo/browser.html`: accessible composer and trust controls.
- `src/study_agent/demo/browser.js`: composer value state and truthful control
  interactions.
- `src/study_agent/demo/browser.py`: allowlisted packaged icons.
- `src/study_agent/demo/icons/`: regular Phosphor assets.
- `tests/unit/demo/test_browser_assets.py`: polish, theme, and composer
  contracts.
- `tests/e2e/test_cardine_browser_contract.py`: 48 px compact rail contract.

## Verification

- `node --check src/study_agent/demo/browser.js`: passed.
- `pytest -q tests/unit/demo/test_browser_assets.py tests/unit/demo/test_browser.py tests/e2e/test_cardine_browser_contract.py`: 17 passed, 4 socket-dependent tests skipped by the sandbox.
- `python -m pip wheel . --no-deps --no-build-isolation -w /private/tmp/cardine-wheel-final`: passed.
- Wheel inspection: `arrow-up.svg`, `book-open.svg`, `caret-down.svg`, and
  `LICENSE.phosphor.txt` are packaged.
- Browser, desktop: expanded/collapsed rail, empty/value composer, send reveal,
  Enter submit, Shift+Enter newline, source navigation, and provenance dialog
  passed.
- Browser, mobile 390×844: modal drawer, backdrop, inert main content, compact
  overrides, and close control passed.
- Visual comparison: source and candidate captured at 1280×720 in equivalent
  states; no P0/P1 finding remained after the second pass.

## Notes

- Anthropic's proprietary fonts and icon font were not copied. Cardine uses
  metric-matched system serif/sans stacks and licensed Phosphor icons.
- Hosting and authentication remain intentionally outside this pass.
