# Log: pinned lesson answer publication

Date: 2026-08-13 14:58 CEST
Area: Cardine / grounded chat

## Summary

Diagnosed the latest live pinned-lesson turns from their durable capability
runs. Retrieval and explanation had completed successfully, but presentation
recovery repeated a locator and quote after every supported segment. The live
answer grew from 2,638 to 7,403 characters and exceeded the 4,000-character
chat receipt contract, so the UI received the generic failure message.

The recovery path now preserves the complete model answer and appends one
deduplicated, size-bounded list of verified source locators. All canonical
source, revision, and chunk identifiers remain attached to the internal
receipt. A genuinely overlong answer still fails closed instead of being
silently truncated.

## Files Changed

- `src/cardine/cli/repository.py`: compact verified-source rendering while
  preserving complete canonical receipt identities.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: public
  lesson-search, lesson-select, pinned-chat regression with repeated citations
  and a complete multi-segment answer.

## Verification

- Red proof before the production change: the pinned turn completed internally
  but the timeline contained `Non sono riuscito...` instead of the answer.
- Focused grounded-chat lane: 11 passed.
- Wider related lane: 35 passed, 2 socket-sandbox skips, 1 pre-existing AnyDoc
  PDF conversion failure unrelated to this change.
- Ruff: passed.
- `MYPYPATH=src .venv/bin/python -m mypy -p cardine.cli.repository`: passed.
- `git diff --check`: passed.
- Replay of the stored live run: 2,638 answer characters, 3,707 published
  characters, 14 canonical identifiers preserved, publication succeeds.

## Notes

- This is presentation compaction only; retrieval, PageIndex, model prompts,
  and capability execution are unchanged.
- The live server must be restarted to load the Python change.
