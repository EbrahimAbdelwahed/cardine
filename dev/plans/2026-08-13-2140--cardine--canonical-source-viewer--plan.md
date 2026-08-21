# Plan: canonical source viewer

Date: 2026-08-13 21:40 CEST
Area: Cardine / sources and chat

## Goal

Complete the existing provenance sheet so a learner can open an authorized
PDF or Markdown source from the Sources page and from a verified chat citation.

## Scope

- In scope: one authenticated read-only source-content endpoint, canonical
  source/revision binding, native PDF display, safe Markdown display, page
  navigation from citations, and focused browser/application tests.
- Out of scope: PDF.js, annotations, search inside the viewer, source editing,
  downloads, and a generic document-management subsystem.

## Approach

1. Make structured citation chips expose a source-viewer action while keeping
   the existing collapsed disclosure and safe labels.
2. Expose verified canonical source bytes through the repository application
   and browser transport, rejecting retired, missing, or mismatched revisions.
3. Enrich materials and presentation DTOs with bounded viewer references.
4. Reuse the existing side sheet: native same-origin PDF frame for extracted
   PDFs and the existing escaped Markdown renderer for text sources.

## Risks

- The worktree has concurrent latency and bounded-agent-loop changes. Edits
  must stay in viewer-specific regions and preserve all unrelated diffs.
- Current presentation text stores locators, not a structured citation DTO.
  Viewer references must be reconstructed only when a locator uniquely matches
  a canonical source title; ambiguous matches remain non-clickable.
- Source blobs are sensitive course material. Reads must preserve private-mode
  authentication and never accept filesystem paths from the browser.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo/test_ai_primitives.py`
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/demo/test_browser.py tests/e2e/test_cardine_browser_contract.py`
- Focused repository-backed source viewer tests.
- `node --check src/cardine/demo/ai-primitives.js`
- `node --check src/cardine/demo/browser.js`
- `.venv/bin/python -m ruff check <changed Python files and tests>`
- `git diff --check`
