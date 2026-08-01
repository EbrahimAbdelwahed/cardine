# Log: chat-native course creation

Date: 2026-08-01 11:25
Area: product shell and repository UI

## Summary

Added a private chat-native course creation flow. The owner can use the
“Crea un corso” action from Oggi or Chat, or type an Italian creation intent
(for example, `Crea un corso di Fisiologia`). Cardine opens a structured,
explicit confirmation card, then creates the immutable course, starts its
first session, and selects that workspace through one versioned repository UI
command. No LLM is given general repository-write authority.

## Files Changed

- `src/study_agent/demo/ui_application.py`: added the confirmed
  `/api/v1/chat/course-creation` repository command.
- `src/study_agent/demo/browser.py`: made the command unavailable in public demo mode.
- `src/study_agent/demo/browser.js`: added chat actions, natural-language shortcut, confirmation card, and command-search entry.
- `src/study_agent/demo/browser.css`: added focused responsive styles for the confirmation card.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: covered create/start/select, idempotent retry, and confirmation requirement.
- `tests/unit/demo/test_browser_assets.py`: pinned the chat flow markers and route.
- `tests/e2e/test_cardine_private_product_journey.py`: pinned public-demo rejection of the private command.

## Verification

- `python -m pytest -q tests/unit/demo/test_browser_assets.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: 23 passed, 2 socket-sandbox skips.
- `python -m pytest -q tests/e2e/test_cardine_private_product_journey.py tests/e2e/test_cardine_repository_browser_journey.py`: 7 passed with local-socket permission enabled.
- `python -m ruff check src/study_agent/demo/browser.py src/study_agent/demo/ui_application.py tests/unit/demo/test_browser_assets.py tests/integration/demo/TUT08/test_repository_backed_chat.py`: passed.
- `node --check src/study_agent/demo/browser.js`: passed.
- `git diff --check`: passed.

## Notes

- The confirmation card derives opaque technical course/session identifiers
  locally from a per-draft nonce. The owner only needs to provide name,
  language, and first learning goal.
- The command is authenticated and CSRF-protected by the private browser
  surface; public demo returns 404 for it.
