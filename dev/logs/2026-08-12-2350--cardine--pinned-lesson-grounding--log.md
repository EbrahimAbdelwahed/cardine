# Log: Pin-scoped lesson grounding

Date: 2026-08-12 23:50
Area: cardine / Wave A

## Summary

Connected explicit lesson selection to grounded answers. Cardine validates a
complete active `SourcePin` before constructing the configured model adapter,
then wraps canonical retrieval so every evidence item remains wholly inside
the selected source revision and span. Grounding run read dependencies and
replay checks commit the pin fingerprint without changing the Study Agent
grounding schema or event types.

The browser exposes visible lesson search, explicit selection, and pin-scoped
question controls at `/api/v1/lessons/search`, `/api/v1/lessons/select`, and
`/api/v1/lessons/ask`. The CLI accepts the selected pin as JSON through
`ask --lesson-pin`.

## Files Changed

- `src/cardine/application/grounding_ask.py`: pin fingerprint dependency and
  replay binding.
- `src/cardine/cli/repository.py`: complete-pin validation and bounded
  retrieval wrapper.
- `src/cardine/cli/commands.py`, `src/cardine/cli/registry.py`: CLI pin input.
- `src/cardine/demo/ui_application.py`, `src/cardine/demo/browser.js`: shared
  lesson routes and visible browser controls.
- `tests/unit/cli/test_pinned_lesson_grounding.py`: foreign, partial, and forged
  cross-section provider-zero checks plus canonical evidence containment.

## Verification

- `PYTHONPATH=src .venv/bin/python -m pytest -q tests/unit/cli/test_pinned_lesson_grounding.py tests/integration/test_grounding_ask_service.py tests/unit/cardine/knowledge/test_lesson_selection.py`: 25 passed.
- `MYPYPATH=src .venv/bin/mypy --explicit-package-bases ...`: passed.
- `.venv/bin/ruff check ...`: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `scripts/audit_harness_ownership.py --check`: passed, 322 rows.

## Notes

- The pre-existing offline release fixture still fails at provider execution
  because it does not grant the A2 consent event; no consent bypass was added.
- PageIndex remains navigation-only and no Study Agent files or event/schema
  contracts were changed.
