# Handoff: Wave A usable recovery

Date: 2026-08-13 14:10 CEST
Area: Cardine / Wave A / usability

## Current State

Wave A and the required usability fixes 1–4 are reconstructed, reviewed,
committed, and published on `codex/cardine-wave-a-recovery` at `095fb75`.
The durable study repository is `../cardine-wave-a-live`; its selected workspace
is course `course-wave-a`, session `session-live`.

A local-owner-setup server is currently intended to run on
`http://127.0.0.1:8765/`. The repository is configured for
`openai-gpt-5.6-luna`; the runtime credential must be supplied through the
server-owned settings boundary and is never stored in development memory.

## Completed

- Explicit lesson references resolve a unique structural range and retrieve all
  complete canonical chunks in that range.
- Source upload returns after canonical admission and exposes durable background
  FTS/PageIndex status. Superseding uploads remain queued until the newest target
  reaches a terminal state.
- Tutor capability/tool guidance is versioned and forbids promises of future
  execution; natural flashcard requests route immediately while meta questions
  do not.
- Flashcard generation exposes exact settled proposal revision identities and
  navigates the browser to Proposte. Ripasso remains HUMAN-accepted/enrolled only.
- The Cardine `dev/` tree is again self-contained and rooted at `dev/index.md`.

## Remaining

- Fix 5 is optional: chat message copy/edit controls and any scroll-history defect
  should be handled as a separate, targeted UI slice.
- The local AnyDoc large-PDF modifications are not part of `095fb75`; preserve
  them as unrelated dirty work until independently verified and committed.
- Terminal Harness adoption checkpoints beyond the studyable Wave A product
  remain governed by the original Wave A contract.

## Important Context

- Do not use PageIndex summaries as evidence.
- Do not rebuild FTS synchronously inside ordinary tutor turns or source-upload
  responses.
- Do not auto-select ambiguous lesson candidates.
- Do not auto-accept or auto-enroll generated cards.
- Preserve unrelated AnyDoc changes and recovery bundles in the working tree.
- Treat the workspace-level `../dev/` directory as historical archive; new
  Cardine memory belongs here.

## Verification

- Final fix 1–4 suite: 48 passed, 8 socket-sandbox skips, 1 unrelated PDF test deselected.
- Integration closeout after indexing race fixes: 30 passed, 2 socket-sandbox skips, 1 unrelated PDF test deselected.
- Focused superseding-target worker test: passed.
- JavaScript syntax, Ruff, and diff checks: passed.
- Independent semantic review: no open finding after the worker-loop correction.
- Global mypy remains blocked before analysis by the checkout's pre-existing duplicate module discovery.
