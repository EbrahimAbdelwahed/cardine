# Design QA: Cardine chat-centric product shell

Date: 2026-07-29 21:26 CEST
Target: faithful Claude interaction and layout model adapted to Cardine's
medical-study content and honest harness capabilities.

## Evidence

- Claude expanded reference:
  `dev/plans/assets/claude-reference/claude-home-expanded-1440x1000-current.png`
- Same-state comparison:
  `dev/plans/assets/chat-centric-audit/claude-cardine-expanded-comparison.png`
- Final conversation:
  `dev/plans/assets/chat-centric-audit/cardine-conversation-final.png`
- Final mobile drawer:
  `dev/plans/assets/chat-centric-audit/cardine-mobile-sidebar-final.png`
- User-supplied compact-sidebar reference:
  `/var/folders/gq/j51ckj5n2dd55d6jvjkwy80m0000gn/T/codex-clipboard-67f8b8d5-b4bc-4dfd-9be8-2a79b8d1380e.png`

## Matched structure

- Warm off-white full-height workspace and restrained monochrome controls.
- Compact 52 px icon rail and expanded 288 px navigation state.
- Centered 672 px home composer with a 20 px radius and subtle Claude-like
  border/shadow treatment.
- Right-aligned bounded learner bubble, unboxed assistant prose, and persistent
  bottom composer.
- Mobile navigation becomes a full-height modal sheet with an inert,
  substantially dimmed background and explicit close semantics.

## Product-specific adaptations

- Cardine identity, course title, medical-study routes, trust boundary, and
  provenance controls replace Claude branding and product labels.
- The primary action is named **Nuova domanda**, not **Nuova chat**, because the
  current harness has no canonical session-creation command. The interface does
  not promise state it cannot create.
- Study tools remain visually secondary and expose honest unavailable states
  when their owners are absent.

## Findings and resolution

- Fixed malformed message markup that prevented learner and assistant classes
  from applying.
- Removed repeated visible role labels and tightened assistant spacing.
- Increased secondary-text contrast and focused keyboard-guidance contrast.
- Strengthened the mobile backdrop and drawer elevation; hid the underlying
  mobile header while modal navigation is open.
- Synchronized mobile `Apri/Chiudi barra laterale` semantics with the actual
  drawer state.
- Replaced dead recent-session controls with one truthful current-session
  route.

Fresh screenshot critique on the final conversation and mobile drawer reported
no remaining P0, P1, or P2 visual defects.

## Verification

- In-app browser: compact/expanded toggle, Enter send, Shift+Enter newline,
  all study routes, mobile open/close/Escape, focus return, and zero console
  errors.
- `python -m pytest -q tests/unit/demo/test_browser_assets.py
  tests/unit/demo/test_browser.py
  tests/integration/demo/TUT08/test_browser_surface.py
  tests/e2e/test_cardine_browser_contract.py`: 22 passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `python -m ruff check src tests`: passed.
- Wheel built with `python -m pip wheel . --no-deps --no-build-isolation`; all
  HTML/CSS/JS files, ten Phosphor SVGs, and the Phosphor license are present.
  A clean `--target` install of the regenerated `dist/` wheel then loaded all
  ten SVGs through `BrowserSurface.asset(...)` and served all ten successfully
  over the real local HTTP boundary.
- `python -m mypy src tests`: not run because mypy is not installed in the
  workspace interpreter.
- A full repository pytest run was stopped after 767 passing tests because it
  had collected a pre-edit E2E assertion; the current focused surface suite was
  recollected afterward and passed.

Final result: passed.
