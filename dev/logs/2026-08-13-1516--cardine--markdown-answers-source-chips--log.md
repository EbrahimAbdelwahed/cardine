# Log: Markdown answers and source chips

Date: 2026-08-13 15:16 CEST
Area: Cardine / chat presentation

## Summary

Assistant answers now render a safe, dependency-free Markdown subset with
headings, paragraphs, emphasis, ordered and unordered lists, block quotes,
inline code, fenced code, and separators. Raw HTML remains escaped.

The server-owned trailing `Fonti verificate` appendix is removed from prose
and presented as deduplicated, compact chips with the existing book icon. The
source drawer and `Visualizza fonti` action remain deferred.

The latest live answer containing repeated `Fonti:` blocks was recorded at
14:53 CEST, five minutes before the backend publication fix. It was not Luna
quoting chunks on its own: the old Cardine presenter appended locator and
verbatim snippet after every segment. The renderer now recognises this exact
historical format too, hides the snippets, and preserves only source chips, so
already-persisted turns become readable without rewriting canonical events.

## Files Changed

- `src/cardine/demo/ai-primitives.js`: escaped-first Markdown renderer,
  verified-source extraction, legacy-source compatibility, deduplicated chips.
- `src/cardine/demo/ai-primitives.css`: prose hierarchy, lists, quote/code
  treatment, compact inline source chips using existing tokens and icon asset.
- `tests/unit/demo/test_ai_primitives.py`: public renderer contracts for
  Markdown, hostile HTML, new sources, legacy verbatim removal, and deduplication.
- `dev/plans/2026-08-13-1505--cardine--markdown-answer-renderer--plan.md`:
  bounded implementation contract.

## Verification

- Red proof for Markdown/source-chip contract: 1 failed before production code.
- Red proof for legacy verbatim compatibility: 1 failed before compatibility code.
- `tests/unit/demo/test_ai_primitives.py tests/unit/demo/test_browser_assets.py`:
  21 passed.
- Browser contract excluding one pre-existing stale flashcard marker test:
  4 passed, 6 socket-sandbox skips, 1 deselected.
- Node syntax, Ruff, and `git diff --check`: passed.
- Stored live turn 78 replay: five source chips, no `Fonti:` prose, no verbatim
  chunk, final mnemonic paragraph preserved.
- Local visual preview: semantic heading levels, emphasis, list, quote, inline
  code, icon chips, wrapping, and tooltip labels rendered correctly.

## Notes

- `test_selected_lesson_flashcards_and_bulk_decisions_are_reachable` expects
  old `data-lesson-flashcards` browser markers that are absent at the current
  branch baseline; it is unrelated to this renderer diff.
- An external Codex CLI review was not run because the environment rejected
  transmitting the private diff. Local security tests remain the evidence.
