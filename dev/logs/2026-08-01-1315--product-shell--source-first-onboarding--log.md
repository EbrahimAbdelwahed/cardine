# Log: source-first onboarding and responsive chat

Date: 2026-08-01 13:15
Area: product shell

## Summary

- Added a bounded repository-backed source upload command for UTF-8 `.txt` and
  `.md` material. Uploads use the canonical ingestion service and reconcile
  exact browser retries.
- Replaced the empty-course prompt with a source-first guided flow: material,
  exam/goal/time, then a source-derived first topic which is handed to the real
  tutor conversation only after confirmation.
- Made outgoing tutor turns optimistic while preserving the draft on failure and
  preventing duplicate submissions during the pending request.
- Moved Settings to the bottom secondary sidebar area, aligned the search icon,
  and reduced the persistent continuation dock while removing its nonfunctional
  model selector.

## Files Changed

- `src/study_agent/demo/ui_application.py`: source upload boundary and onboarding DTO.
- `src/study_agent/demo/browser.py`: source upload body allowance and private route.
- `src/study_agent/demo/browser.{html,css,js}`: guided UI and interaction polish.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: canonical upload coverage.
- `tests/e2e/test_cardine_repository_browser_journey.py`: source-first and upload journeys.

## Verification

- `node --check src/study_agent/demo/browser.js`: passed.
- `pytest -q tests/unit/demo/test_browser.py tests/unit/demo/test_ui_application.py tests/integration/demo/TUT08/test_repository_backed_chat.py tests/e2e/test_cardine_private_product_journey.py`: 46 passed.
- `pytest -q tests/e2e/test_cardine_repository_browser_journey.py`: 3 passed.
- `git diff --check`: passed.

## Notes

- PDF, image, and other binary source formats remain explicitly unavailable;
  the existing canonical ingestion contract supports text and Markdown only.
