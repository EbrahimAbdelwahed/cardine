# Handoff: pinned lesson live fix

Date: 2026-08-13 14:58 CEST
Area: Cardine / grounded chat

## Current State

The live no-output defect after selecting and attaching Lezione 1 is repaired
on `codex/cardine-wave-a-recovery`. The durable runtime repository remains
`../cardine-wave-a-live`, course `course-wave-a`, session `session-live`.

## Completed

- Confirmed that the live pinned capability retrieved 23 canonical chunks and
  completed an 11-segment explanation.
- Removed repeated per-segment quote expansion from chat presentation.
- Added one compact, deduplicated verified-source list without truncating the
  explanation.
- Preserved all canonical citation identities in the receipt.
- Added a public end-to-end regression for search, selection, attached pin, and
  long grounded answer publication.

## Remaining

- Fix 5 remains optional: message copy/edit and any history-scroll defect.
- A separate synthetic Markdown probe showed a possible PageIndex range issue
  when a lesson heading immediately contains nested headings. It was not part
  of the live failure and was intentionally not mixed into this targeted fix.

## Important Context

- Do not solve this defect by truncating the explanation or discarding
  canonical citation identifiers.
- PageIndex remains navigation only; canonical chunks remain evidence.
- The known AnyDoc PDF fixture failure in the sandbox is unrelated.
- Recovery bundles in the repository root remain untracked and must not be
  committed.

## Verification

- Focused grounded-chat lane: 11 passed.
- Wider related lane: 35 passed, 2 socket-sandbox skips, 1 unrelated PDF failure.
- Ruff, repository mypy, and diff checks: passed.
- Stored live-run replay now fits the chat receipt and would publish.
