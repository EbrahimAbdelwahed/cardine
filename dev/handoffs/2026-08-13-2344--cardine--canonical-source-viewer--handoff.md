# Handoff: canonical source viewer

Date: 2026-08-14 00:06 CEST
Area: Cardine / sources and chat

## Current State

The source viewer implementation and focused regressions are present in the
shared Cardine worktree but are not committed and have not been loaded into the
already-running live server. The Sources route now uses a lateral reading pane;
the floating viewer has a smaller default size and can be resized on desktop.

## Completed

- Canonical authenticated source/revision content endpoint.
- Inline Markdown/text/PDF viewer on the Sources page.
- Floating viewer from structured verified chat citations.
- Sticky right-hand viewer on the Sources page.
- Bounded mouse and keyboard resizing for the floating desktop viewer.
- Native PDF page navigation from canonical locator metadata.
- Safe Markdown rendering through the existing escaped renderer.
- Fail-closed behavior for ambiguous citation-to-revision matches.

## Remaining

- Integrate with the concurrent bounded-agent-loop and latency diffs, then
  commit the intended combined scope.
- Restart the live server before manual browser verification.
- Manually verify a real large PDF citation and a Markdown source on desktop
  and mobile, including the resize handle. The sandbox prevented socket/browser
  visual verification.
- Consider single-range HTTP support only if large native PDFs prove slow.

## Important Context

- Do not replace canonical source/revision identifiers with a filesystem path
  or a browser-owned blob URL.
- Do not make ambiguous historical locators clickable. Structured citation
  reconstruction intentionally requires exactly one matching revision.
- Keep `frame-ancestors 'self'` and `X-Frame-Options: SAMEORIGIN` limited to the
  document response; the rest of the application remains `DENY`/`'none'`.
- Preserve unrelated dirty changes in `browser.js`, `ui_application.py`, and
  repository-backed chat tests.
- Desktop resize is deliberately bounded to the viewport. Mobile remains
  full-height and does not expose the resize handle.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo/test_ai_primitives.py tests/unit/demo/test_browser_assets.py tests/unit/demo/test_browser.py`: 34 passed.
- Viewer-focused integration slice: 6 passed.
- Ruff, Node syntax, and diff checks: passed.
- Lateral/resizable follow-up: 35 touched UI tests passed; see
  `dev/logs/2026-08-14-0006--cardine--source-viewer-layout--log.md`.
