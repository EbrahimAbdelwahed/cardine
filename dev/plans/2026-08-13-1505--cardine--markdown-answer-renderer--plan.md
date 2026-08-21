# Plan: Markdown answer renderer

Date: 2026-08-13 15:05 CEST
Area: Cardine / chat presentation

## Goal

Render model answers as readable, safe Markdown and present the existing
`Fonti verificate` appendix as compact source chips instead of plain text.

## Scope

- In scope: dependency-free safe Markdown subset for assistant answers;
  headings, paragraphs, emphasis, lists, quotes, inline/fenced code; compact
  non-interactive source chips; responsive typography; regression tests.
- Out of scope: source drawer, `Visualizza fonti`, citation navigation,
  canonical receipt changes, model prompt changes, new dependencies.

## Approach

1. Pin the public `CardineAI.answer` rendering contract with a failing Node
   execution test, including hostile HTML.
2. Add the smallest escaped-first Markdown renderer and extract only the
   server-owned trailing verified-source appendix.
3. Style the prose and chips with existing design tokens and icon assets.
4. Verify JavaScript syntax, focused UI tests, browser contracts, Ruff, and the
   live server after restart.

## Risks

- Model text is untrusted: raw HTML must remain escaped.
- The source parser must only consume the exact trailing appendix format and
  leave ordinary prose untouched.
- Full CommonMark compliance is not required; unsupported syntax must remain
  legible as text.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo/test_ai_primitives.py tests/unit/demo/test_browser_assets.py tests/e2e/test_cardine_browser_contract.py`
- `node --check src/cardine/demo/ai-primitives.js`
- `.venv/bin/ruff check tests/unit/demo/test_ai_primitives.py`
- `git diff --check`
