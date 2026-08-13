# Cardine development memory

Updated: 2026-08-13 14:10 CEST

This file is the authoritative entrypoint for Cardine development memory.
Cardine-specific plans, logs, notes, and handoffs belong in this repository's
`dev/` tree. The workspace-level `../dev/` tree is historical orchestration
archive and must not be treated as Cardine's current state.

## Start here

- [Current Wave A handoff](handoffs/2026-08-13-1410--cardine--wave-a-usable-recovery--handoff.md) — shipped state, live runtime, remaining work, and verification boundary.
- [Wave A product contract](plans/2026-08-11-2345--cardine--wave-a-studyable-product--plan.md) — original product invariants and release definition.
- [Wave A recovery plan](plans/2026-08-12-1930--cardine--wave-a-text-knowledge-recovery--plan.md) — reconstruction contract after the temporary worktree loss.
- [Wave A closure log](logs/2026-08-13-0200--cardine--wave-a-product-closure--log.md) — recovered study journey and qualification evidence.
- [Usability fixes 1–4 log](logs/2026-08-13-1305--cardine--rapid-usability-fixes-1-4--log.md) — structural lesson retrieval, observable indexing, robust routing, and flashcard feedback.
- [Live lesson-turn diagnosis](logs/2026-08-13-1339--cardine--live-lesson-turn-no-output--log.md) — reproduced causes behind the failed “studiamo la lezione 1” journey; production remains unfixed.
- [Memory consolidation log](logs/2026-08-13-1420--cardine--dev-memory-consolidation--log.md) — why this repository-local index is now the sole current entrypoint.

## Durable decisions

- Canonical source bytes, events, citations, artifact decisions, and recall
  history are authoritative. FTS and PageIndex are rebuildable derived state.
- PageIndex provides structure and navigation only. Claims and citations always
  resolve to canonical source chunks.
- Source admission must complete independently of derived indexing. Indexing is
  durable, restart-safe, observable, and may degrade to lexical retrieval.
- Generated flashcards remain proposals. Only explicit HUMAN acceptance and
  enrollment make them eligible for recall.
- Provider-backed natural-language processing uses the configured
  `openai-gpt-5.6-luna` adapter through the server-owned credential boundary.
- The browser is presentation and transport. It does not own canonical study
  state or call model providers directly.

## Recovery history

- [Temporary worktree-loss handoff](handoffs/2026-08-12-1515--cardine--wave-a-temporary-worktree-loss--handoff.md) explains why Wave A was reconstructed in a durable checkout.
- Detailed historical plans and logs remain under [`plans/`](plans/) and
  [`logs/`](logs/). They are evidence, not a substitute for the current handoff.
- Reusable operational findings live under [`notes/`](notes/).

## Local continuation lane

- [AnyDoc large mixed-PDF plan](plans/2026-08-12-1830--cardine--anydoc-large-mixed-pdf--plan.md) and [verification log](logs/2026-08-12-1900--cardine--anydoc-large-mixed-pdf--log.md) describe the preserved local PDF work. The current handoff owns whether that work is published; the log alone must not be read as branch status.

## Memory maintenance rule

At the end of a material Cardine change:

1. write or update the focused log;
2. replace the current handoff when the operational state changes;
3. update this index only when the recommended entrypoints or durable decisions change;
4. commit and push the memory with the code it describes whenever possible.

Never leave the only current Cardine handoff in the workspace-level archive or
in a temporary worktree.
