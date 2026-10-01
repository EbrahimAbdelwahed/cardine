# Log: source viewer layout and resizing

Date: 2026-08-14 00:06 CEST
Area: Cardine / sources and chat

## Summary

Moved the Sources-page viewer from below the source list into a dedicated
right-hand reading pane. Reduced the floating viewer's initial footprint and
made it user-resizable on desktop from a visible lower-left handle. The handle
also supports arrow-key resizing; Shift increases the step. Mobile keeps the
full-height viewer and intentionally hides the resize affordance.

## Files Changed

- `src/cardine/demo/browser.js`: lateral Sources-page structure, responsive
  viewer navigation, and bounded pointer/keyboard resizing.
- `src/cardine/demo/browser.css`: two-column source layout, sticky reading
  pane, smaller floating defaults, viewport bounds, and resize affordance.
- `src/cardine/demo/browser.html`: resize control on the floating viewer.
- `tests/unit/demo/test_browser_assets.py`: source-viewer layout contract.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo/test_browser_assets.py tests/unit/demo/test_ai_primitives.py tests/unit/demo/test_browser.py`: 35 passed.
- `node --check src/cardine/demo/ai-primitives.js`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `git diff --check`: passed.

## Notes

- The live server was not restarted from this side conversation.
- The worktree still contains unrelated untracked recovery bundles; they were
  not modified.
