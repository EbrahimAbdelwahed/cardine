# Handoff: conversation memory tools

Date: 2026-08-14 00:44 CEST
Area: Cardine / tutor runtime

## Current State

The shared worktree contains a verified bounded conversation-memory trajectory:
recent-window metadata, private search/read tools, prompt 1.5.0, tool-first
flashcard routing, source-only factual grounding, v3 privacy-safe handoffs, and
post-routing structural traces.

## Completed

- Search and cursor reads over canonical current-session conversation.
- Tool reads bound to the exact tutor snapshot high-water sequence.
- Broad-history flashcard requests read before the oldest included recent turn.
- Raw excerpts remain same-turn context and are absent from durable handoffs.
- Arbitrary model summaries are replaced by host-normalized topic terms/counts.
- Flashcard claims remain canonical-source grounded; lesson pins still constrain.
- Structural traces describe decisions actually executed after host routers.
- v1/v2 completion handoff decoding remains byte-stable; new records use v3.

## Remaining

- Exercise a real long-conversation flashcard request against Luna and inspect
  Tool Chips plus `/api/v1/diagnostics/turns`.
- Do not build a semantic training corpus until consent/export requirements are
  separately approved.

## Important Context

- The existing four-decision budget is unchanged.
- `conversation.search` is lexical; a second distinct query/read is available
  when needed, but embeddings remain deliberately deferred.
- Exact retry requires the final capability query/scope; retrieved excerpts are
  not required and are not persisted.
- Concurrent source-viewer edits in the same worktree belong to another chat.

## Verification

- Final focused suite: 102 passed, plus a separate 51-pass
  high-water/tool-loop slice.
- Ruff and diff check: passed.
- Focused mypy: only two pre-existing `turn_activity.py` diagnostics remained.
