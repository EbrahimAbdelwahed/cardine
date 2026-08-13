# Cardine development memory

Updated: 2026-08-13 23:44 CEST

This file is the authoritative entrypoint for Cardine development memory.
Cardine-specific plans, logs, notes, and handoffs belong in this repository's
`dev/` tree. The workspace-level `../dev/` tree is historical orchestration
archive and must not be treated as Cardine's current state.

## Start here

- [Canonical source viewer handoff](handoffs/2026-08-13-2344--cardine--canonical-source-viewer--handoff.md) and [implementation log](logs/2026-08-13-2344--cardine--canonical-source-viewer--log.md) — inline Sources-page PDF/Markdown viewing and floating citation viewer over authenticated canonical revisions; awaiting shared-worktree integration and live restart.
- [Bounded agent-loop handoff](handoffs/2026-08-13-2145--cardine--bounded-agent-loop--handoff.md) and [implementation log](logs/2026-08-13-2145--cardine--bounded-agent-loop--log.md) — same-turn tool observations, exact duplicate suppression, four-decision budget, and restart-safe tool-informed capability handoffs.
- [Targeted latency-reduction handoff](handoffs/2026-08-13-2036--cardine--latency-reduction--handoff.md) and [measurement log](logs/2026-08-13-2036--cardine--targeted-latency-reduction--log.md) — coherent snapshot fast path, bounded Luna context, faster FTS result resolution, immediate receipt rendering, and responsive reads.
- [Current live Tool Chips handoff](handoffs/2026-08-13-1647--cardine--live-tool-chips--handoff.md) — truthful process-local tutor activity, polling boundary, verification, and deferred reload work.
- [Live Tool Chips ADR](decisions/2026-08-13--ADR-0001--process-local-turn-activity.md) and [implementation log](logs/2026-08-13-1647--cardine--live-tool-chips--log.md) — privacy contract and shipped first delivery.
- [Tool Chips empty-dialogue diagnosis](logs/2026-08-13-1805--cardine--tool-chips-empty-dialogue--log.md) — live evidence and the targeted model-activity correction.
- [Circular clarification routing diagnosis](logs/2026-08-13-2000--cardine--circular-clarification-routing--log.md) — why answered tutor questions still produce another clarification, with the bounded repair direction.
- [Bounded clarification recovery handoff](handoffs/2026-08-13-2030--cardine--bounded-clarification-recovery--handoff.md) and [implementation log](logs/2026-08-13-2030--cardine--bounded-clarification-recovery--log.md) — shipped prompt 1.3.2 and one semantic retry for answered tutor questions.
- [Current collapsed-sources handoff](handoffs/2026-08-13-1531--cardine--collapsed-sources--handoff.md) — current branch, live runtime, default source disclosure, and remaining work.
- [Markdown chat-presentation handoff](handoffs/2026-08-13-1516--cardine--markdown-chat-presentation--handoff.md) — safe Markdown answers and source-chip renderer boundary.
- [Pinned-lesson live-fix handoff](handoffs/2026-08-13-1458--cardine--pinned-lesson-live-fix--handoff.md) — completed publication fix and its verification boundary.
- [Wave A usable-recovery handoff](handoffs/2026-08-13-1410--cardine--wave-a-usable-recovery--handoff.md) — shipped fixes 1–4 and their verification boundary.
- [Wave A product contract](plans/2026-08-11-2345--cardine--wave-a-studyable-product--plan.md) — original product invariants and release definition.
- [Wave A recovery plan](plans/2026-08-12-1930--cardine--wave-a-text-knowledge-recovery--plan.md) — reconstruction contract after the temporary worktree loss.
- [Wave A closure log](logs/2026-08-13-0200--cardine--wave-a-product-closure--log.md) — recovered study journey and qualification evidence.
- [Usability fixes 1–4 log](logs/2026-08-13-1305--cardine--rapid-usability-fixes-1-4--log.md) — structural lesson retrieval, observable indexing, robust routing, and flashcard feedback.
- [Live lesson-turn diagnosis](logs/2026-08-13-1339--cardine--live-lesson-turn-no-output--log.md) — reproduced causes behind the failed “studiamo la lezione 1” journey.
- [Lesson routing usability fix](logs/2026-08-13-1404--cardine--lesson-routing-usability--log.md) — targeted structural aliases, canonical evidence, and one-turn natural lesson study.
- [Pinned lesson answer publication](logs/2026-08-13-1458--cardine--pinned-lesson-answer-publication--log.md) — live diagnosis and targeted repair for completed explanations discarded by citation expansion.
- [Markdown answers and source chips](logs/2026-08-13-1516--cardine--markdown-answers-source-chips--log.md) — safe Markdown rendering, compact sources, and cleanup of historical verbatim citation blocks.
- [Collapsed source disclosure](logs/2026-08-13-1531--cardine--collapsed-source-disclosure--log.md) — closed-by-default source count with expandable locator chips.
- [Chat-attached lesson pin handoff](handoffs/2026-08-13-1404--cardine--chat-attached-lesson-pin--handoff.md) and [log](logs/2026-08-13-1404--cardine--chat-attached-lesson-pin--log.md) — the lesson picker is now a composer attachment and the pin travels with the chat turn; uncommitted working-tree state.
- [Dead browser-asset gates note](notes/2026-08-13-1404--tests--demo-asset-gates-pointed-at-pre-rename-path--note.md) — the `tests/unit/demo` UI gate resolved a pre-rename path and never ran; repointed and green again.
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
- Cardine currently runs its copied `src/study_agent` core. Harness `0.3.0` is
  the adoption artifact, but installed-distribution parity (CA-08) and
  copied-core removal (CA-10) are still pending; see [`../CONTEXT.md`](../CONTEXT.md).

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
