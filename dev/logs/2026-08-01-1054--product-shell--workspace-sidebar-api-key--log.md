# Log: workspace, sidebar, and model credential fixes

Date: 2026-08-01 10:54
Area: product shell

## Summary

Fixed the collapsed sidebar account control geometry so it uses the same 40px
control grid as search and navigation icons. Added repository-backed course and
session management to the private settings surface, including listing the
workspace, selecting an existing course/session, creating a course, and starting
a session for an explicitly selected course. Added a minimal user-triggered model connection check that never
includes repository content. The live private repository was migrated from the
old DeepSeek-compatible configuration to the `openai-gpt-5.6-luna` adapter bound
to `OPENAI_API_KEY`; runtime pasted keys are trimmed before use.

## Files Changed

- `src/study_agent/demo/browser.css`: align collapsed account/search controls.
- `src/study_agent/demo/browser.js`: render workspace/settings management and model check actions.
- `src/study_agent/demo/product_settings.py`: use the canonical Luna adapter id and trim runtime credentials.
- `src/study_agent/demo/ui_application.py`: expose workspace and model-check routes.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: cover workspace mutations and minimal model check.
- `tests/unit/demo/test_browser_assets.py`: pin UI route/marker and sidebar contracts.
- `tests/unit/demo/test_product_settings.py`: pin credential trimming and canonical adapter metadata.

## Verification

- `python -m pytest -q tests/unit/demo/test_product_settings.py tests/unit/demo/test_browser_assets.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: 42 passed, 2 sandbox socket skips.
- `python -m pytest -q tests/e2e/test_cardine_private_product_journey.py tests/e2e/test_cardine_repository_browser_journey.py tests/unit/demo/test_private_production_packaging.py`: 9 passed with local-socket permission enabled.
- `python -m ruff check src/study_agent/demo src/study_agent/cli/repository.py tests/unit/demo tests/integration/demo/TUT08/test_repository_backed_chat.py`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.
- `python -m pytest -q tests/unit/demo/test_browser_assets.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: 20 passed, 2 sandbox socket skips after the course selector refinement.
- Local preview `http://127.0.0.1:8765/`: `/health`, private login, `/api/v1/settings`, `/api/v1/workspace`, write-only credential set/remove, and safe model-check configuration error verified.

## Notes

- The runtime API key is intentionally process-local and was cleared on the
  preview restart. Re-enter the real key in Settings, then use “Verifica
  connessione”. The preview currently has no credential configured.
- The repository-backed model check sends only `Reply only with OK.` after the
  user explicitly clicks the control; no study material or session history is
  sent by that diagnostic route.
