# Handoff: Markdown chat presentation

Date: 2026-08-13 15:16 CEST
Area: Cardine / chat presentation

## Current State

Cardine's assistant-answer primitive renders safe Markdown and source chips.
The durable runtime remains `../cardine-wave-a-live`, course `course-wave-a`,
session `session-live`. Reloading the browser is sufficient because the server
reads packaged static assets on each request.

## Completed

- Markdown hierarchy and common study-answer formatting.
- Escaped-first rendering for untrusted model content.
- Compact book-icon chips for current verified-source appendices.
- Visual compatibility for pre-fix turns containing repeated verbatim chunks.
- Deduplication and full locator tooltip on visually truncated chip labels.

## Remaining

- `Visualizza fonti` and a source drawer are intentionally deferred.
- Fix 5 remains optional: message copy/edit and any history-scroll defect.
- The stale selected-lesson flashcard browser contract should be reconciled in
  its own feature lane rather than hidden in this UI change.

## Important Context

- Do not persist rendered HTML; canonical presentations remain plain bounded
  text and canonical citation identities remain server-owned.
- Do not add raw HTML support to the Markdown renderer.
- Do not remove legacy parsing until existing canonical presentations have an
  explicit migration or a structured citation DTO supersedes it.
- Recovery bundles in the repository root remain untracked.

## Verification

- Focused UI tests: 21 passed.
- Remaining browser contract lane: 4 passed, 6 socket skips.
- Live historical answer replay: clean body and five deduplicated chips.
- Node syntax, Ruff, and diff checks: passed.
