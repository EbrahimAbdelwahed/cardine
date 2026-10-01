# Log: canonical source viewer

Date: 2026-08-13 23:44 CEST
Area: Cardine / sources and chat

## Summary

Recovered the existing provenance-sheet interaction and completed it as a
read-only canonical source viewer. The Sources page opens Markdown/text or a
native PDF inside the page; structured verified citations open the same
revision in a floating side sheet. PDF citations carry the canonical page and
open the browser viewer at that page.

Source content is served only through a course-owned source/revision pair.
Private-mode authentication is preserved, retired sources fail closed, blob
integrity remains owned by the canonical repository adapters, and same-origin
framing is enabled only for the document response.

## Files Changed

- `src/cardine/demo/ui_application.py`: canonical document DTO/read boundary,
  viewer metadata, and fail-closed structured citation reconstruction.
- `src/cardine/demo/browser.py`: authenticated binary document transport and
  same-origin PDF framing policy.
- `src/cardine/demo/browser.html`: shared floating source viewer sheet.
- `src/cardine/demo/browser.js`: inline Sources-page viewer and floating chat
  viewer, including PDF page fragments and safe Markdown rendering.
- `src/cardine/demo/browser.css`: responsive source-viewer surfaces.
- `src/cardine/demo/ai-primitives.js`: structured clickable citation controls
  and reuse of the existing safe Markdown renderer.
- `src/cardine/demo/ai-primitives.css`: button-compatible citation styling.
- Focused unit, integration, and browser-contract tests for the new seams.

## Verification

- Viewer-focused regression slice: 6 passed.
- Complete touched UI unit files: 34 passed.
- Broader browser/repository slice: 65 passed, 9 socket skips, 3 unrelated
  existing failures (stale selected-lesson flashcard asset contract, AnyDoc
  worker conversion, restarted Tool Chips activity-receipt parity).
- Ruff on changed Python/tests: passed.
- `node --check` on both changed JavaScript files: passed.
- `git diff --check`: passed.
- Focused mypy reached only pre-existing concurrent Tool Chips/repository/UI
  errors; it reported no new source-viewer type error.

## Notes

- No PDF.js dependency was added. The first delivery deliberately uses the
  browser's native PDF viewer and sends the complete verified blob.
- HTTP Range support, annotations, document search, downloads, and source
  editing remain out of scope.
- The shared worktree contains concurrent latency and bounded-agent-loop work;
  this change was not committed or used to restart the live server from the
  side conversation.
