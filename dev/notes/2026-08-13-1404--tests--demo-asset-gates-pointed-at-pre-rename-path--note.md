# Note: the browser-asset test gates were silently dead

Date: 2026-08-13 14:04 CEST
Area: Cardine / tests / demo shell

## Context

Four test modules still resolved the demo package at the pre-rename path
`src/study_agent/demo`, which has not existed since the package became
`cardine.demo`:

- `tests/unit/demo/test_design_system.py`
- `tests/unit/demo/test_browser_assets.py`
- `tests/unit/demo/test_ai_primitives.py`
- `tests/unit/demo/test_private_access.py` (a `monkeypatch.setattr` target string)

The first three read their assets at import time, so they failed during
collection with `FileNotFoundError` rather than as assertions, which reads as
"broken environment" instead of "no coverage". The fourth failed with an
`ImportError` from an unresolvable monkeypatch target. Together they are the
only automated gate over `browser.css` / `browser.js` / `browser.html`: token
usage, route allowlists, semantic markers, motion and theme safety. Every UI
change since the rename shipped without any of it running.

Repointing the four paths to `src/cardine/demo` makes all 32 tests pass with no
other edit, so the gate itself was still correct — only its address was stale.

## Implication

- A collection error in `tests/unit/demo` is not background noise: it means the
  UI gate is not running at all. Treat it as a failure, not as an environment
  quirk.
- After any package rename, grep tests for the old dotted path *and* the old
  filesystem path, including monkeypatch target strings, which no import or type
  checker will catch.
- When a UI slice is verified only "by eye", check first that these tests
  actually ran.

## References

- `tests/unit/demo/test_design_system.py`
- `tests/unit/demo/test_browser_assets.py`
- `tests/unit/demo/test_ai_primitives.py`
- `tests/unit/demo/test_private_access.py`
- [chat-attached lesson pin log](../logs/2026-08-13-1404--cardine--chat-attached-lesson-pin--log.md)
