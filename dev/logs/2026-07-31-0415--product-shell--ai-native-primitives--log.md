# Log: Cardine AI-native UI primitives

Date: 2026-07-31 04:15
Area: product shell

## Summary

Adapted all 17 useful AI chat patterns inventoried from Beautiful UI into the
existing Cardine workspace. The implementation is dependency-free, preserves
the Claude-inspired chat shell, and connects each surface to a real Cardine DTO
or an explicit unavailable/local-only state.

The final review also corrected public-demo status data being presented as
conversation, IME command-palette handling, one-time screen-reader
announcements for answer reveal, follow-up composer targeting, and mobile
drawer inert-state recovery.

## Files Changed

- `src/study_agent/demo/ai-primitives.js`: reusable renderers and scoped
  interaction binders.
- `src/study_agent/demo/ai-primitives.css`: Cardine-native pattern styling,
  responsive behavior, and reduced-motion support.
- `src/study_agent/demo/browser.html`: primitive assets, sidebar search, and
  command palette.
- `src/study_agent/demo/browser.js`: real route/DTO mappings for all patterns.
- `src/study_agent/demo/browser.py`: asset serving and allowlisting.
- `src/study_agent/demo/ui_application.py`: truthful separation of public-demo
  status trace from conversational timeline.
- `src/study_agent/demo/icons/magnifying-glass.svg`: packaged Phosphor icon.
- `tests/unit/demo/test_ai_primitives.py`: rendering, keyboard, lifecycle,
  IME, reveal, follow-up, and dependency-boundary coverage.
- `tests/unit/capabilities/test_gateway_worker_adapter.py`: accepted the
  intentional grounded-answer schema fingerprint already documented by the
  Luna adapter bead.
- `design-qa.md`: same-input visual comparison and final QA record.
- `specs/adaptive-tutor/beads/TUT-08I-ai-native-ui-primitives.md`: completed
  acceptance criteria.

## Verification

- `python -m pytest -q tests/unit/demo tests/integration/demo/TUT08
  tests/e2e/test_cardine_browser_contract.py
  tests/e2e/test_cardine_repository_browser_journey.py`: 101 passed.
- `python -m pytest -q`: 2067 passed, 13 skipped.
- `python -m ruff check src tests`: passed.
- `git diff --check`: passed.
- `python -m pip wheel . --no-deps --no-build-isolation`: passed; wheel
  SHA-256 `c6a6a2027470792c6897831e512c3a8fc6ba45f84d6522a161702b5006a8fe81`.
- Clean wheel install and `BrowserSurface.asset(...)` checks: passed.
- In-app browser desktop/mobile, route, command, filter, composer, recovery,
  and console checks: passed.
- Independent final review: zero P0/P1/P2 findings.

## Notes

- GPT-5.6 Luna remains the selected base adapter. A live provider response
  still requires `OPENAI_API_KEY`; its opt-in smoke test is one of the expected
  skips when no credential is present.
- Hosting and authentication remain intentionally out of scope until the user
  chooses the deployment path.
