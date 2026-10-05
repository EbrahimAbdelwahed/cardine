# Cardine development memory

Updated: 2026-10-05 CEST

This file is the authoritative entrypoint for Cardine development memory.
Cardine-specific plans, logs, notes, and handoffs belong in this repository's
`dev/` tree. The workspace-level `../dev/` tree is historical orchestration
archive and must not be treated as Cardine's current state.

## Start here

- [Page-load regression budgets](logs/2026-10-04--cardine--page-load-latency--log.md) — browser thresholds pinned to measured real-course desktop/mobile startup and page results; API ceiling and deterministic work guards retained.
- [Jev projection latency](handoffs/2026-10-04--jev-projection-latency.md) — full canonical verification with bounded line/node preparation, selected-chunk projection and shared semantic cache preparation; source-free before/after evidence and PR #24, no live rollout.

- [Retrieval reliability audit](audits/2026-10-04--retrieval-reliability.md) — owner-requested needle benchmark on the live 559-page source; definition-only chimotripsin retrieval reproduced, 21 positive cases and two negatives, bounded keyword/context experiments and no runtime policy change.

- [Visible study-note progress](logs/2026-10-04--cardine--notes-generation-progress--log.md) — active lesson/segment feedback and completed counts, stacked on PR #18's automatic PageIndex picker; live rollout pending.

- [Flashcard context and settlement repair](logs/2026-10-04--cardine--flashcard-context-reliability--log.md) — current-topic canonical planning, bounded Jev conversation/tool context and truthful evidence-limit settlement; local rollout pending.

- [Immediate flashcard review](logs/2026-10-03--cardine--flashcard-review-latency--log.md) — next card advances locally while canonical ratings save in order; exact retries, pending/error states and measured local HTTP/browser latency.
- [Source structure dropdown and scoped notes](logs/2026-10-02--cardine--lesson-structure-picker--log.md) — ready PageIndex spans feed a direct lesson dropdown, exact scoped extraction and restart-safe note proposals; 2,910 offline tests and package/static gates pass; PR #18 published, CI and automatic review pending.
- [GPT-6 Luna and large-document PageIndex](logs/2026-10-02--cardine--gpt6-luna-pageindex--log.md) — owner-requested local upgrade, separate model identity and bounded indexing for 700 headings.
- [Sources layout](logs/2026-10-02--cardine--sources-layout--log.md) — ordered library/reader composition, responsive actions, canonical provenance and PageIndex integration seam.

- [Local latency and native provider drafts](logs/2026-10-02--cardine--local-latency-streaming--log.md) — owner-requested profiling, exact-byte read caches, route-specific work and actual provider SSE; offline turn reduced from 49.8 to 6.2 seconds, live rollout pending.
- [Selected PDF lesson notes](logs/2026-10-02--cardine--selected-lesson-notes--log.md) — boundary editing and explicit lesson selection, independent generation and retry identity.

- [Scheduled recall by default](logs/2026-10-02--cardine--recall-default--log.md) — standard server FSRS composition integrated with main, including the merged student journal and source-note generation.

- [Minimal student journal](logs/2026-10-01--cardine--student-journal--log.md) — owner-approved replacement
  for learner-evidence estimates, session-note memory writes and product context conflicts.
  Student history uses one append-only file/service; recall remains separate.
- [Jev/PageIndex integration](../specs/done/jev-pageindex/README.md) — PR #10: OpenRouter Decisions, shared canonical indexing and semantic routing; merge validation in progress.

- [Owner-authorized merges and review limit](handoffs/2026-10-01--review-limit-and-owner-merges.md) — PRs #5/#3/#7/#8/#9 merged; remaining automatic findings recorded; future review stops after two rounds per PR.
- [Source, PDF and audio study notes](logs/2026-09-30--cardine--source-study-notes--log.md) — approved source-page generation, Groq Turbo transcription, editable PDF lesson ranges, review and dependency-aware publication; based on PR #5.
- [PR #9 current review repairs](handoffs/2026-10-01--cardine--pr9-current-review-repairs--handoff.md) — stale accepted material publication and oversized audio manifest handling, with scoped verification and submission status.
- [Combined recent-PR test preview](logs/2026-09-28--cardine--combined-pr-preview--log.md) — isolated integration of PRs #3/#5/#6/#7, copied study data, local port 8766 and the remaining custody gate.
- [Compact Thinking and Streaming Text](logs/2026-09-28--cardine--compact-thinking-streaming--log.md) — approved compact activity renderers, progressive verified answers, follow-ups and response actions on the recovery base.
- [PR #3 automatic review fixes](handoffs/2026-10-01--cardine--pr3-review-fixes--handoff.md) — explicit current-turn lesson scope remains authoritative after model query distillation, and unsuccessful normal turn receipts settle Tool Chips as failed.
- [PR #5 automatic review follow-up](handoffs/2026-10-01--cardine--pr5-review-fixes.md) — history-derived flashcard scope is rebuilt only from validated bounded conversation observations; material emphasis and Italian uncertainty commitments are preserved and checked.

- [Flashcard generation repair publication](logs/2026-09-29--cardine--flashcard-generation-publication--log.md) — the owner authorized publishing the generation repair to PR #5; empty-array wire schema, truthful failure and new-turn retry regressions verified offline; preview update and live generation remain pending.

- [Message Scroller and BeUI evaluation](logs/2026-09-28--cardine--message-scroller--log.md) — reader-aware chat navigation implemented; Citations and Prompt Input assessed with canonical source boundaries.

- [Failed-turn observability](logs/2026-09-28--cardine--failed-turn-observability--log.md) — correlated, bounded failure/retry traces with safe transport and execution metadata; the owner approved the six-path custody update.

- [Approved recovery custody and package target](logs/2026-09-28--cardine--approved-recovery-custody--log.md) — owner accepted the exact recovery bytes; installed-package parity and copied-core removal remain required.

- [Recovery CI alignment and decision feedback](logs/2026-09-28--cardine--recovery-ci-alignment--log.md) — current development branch includes main; flashcard acceptance feedback is fixed, typing and package checks pass, ownership approval is recorded in the newer custody log; publication and live generation reproduction remain pending.

- [Page-aware flashcard locator fix](logs/2026-08-15-0040--cardine--page-aware-flashcard-locator-fix--log.md) — repeated live flashcard turns selected the correct capability but failed before Luna because planning omitted immutable PDF page provenance from the locator; planner and resolver now share one canonical formatter while exact integrity validation remains fail-closed.
- [Password-free runtime API key UI](logs/2026-08-14-2352--cardine--password-free-runtime-api-key-ui--log.md) — the loopback `local_repository` shell has no password gate but exposes write-only, process-local OpenAI credential settings backed by the same store used by Luna; local mutations require exact same-origin requests.
- [Atomic capability progress message](logs/2026-08-14-2223--cardine--atomic-capability-progress-message--log.md) — Luna can atomically select a safe host-owned progress sentence with `start_capability`; it is authenticated, process-local, non-canonical, non-durable, and does not weaken host-owned execution.
- [Restart-safe paired material generation](logs/2026-08-14-1935--materials--restart-safe-paired-generation--log.md) and [four-slice spec](../specs/material-generation-workflow/README.md) — Slices 01–02 now provide lesson-material lineage plus Luna-only checkpointed complete/study proposals; the approved 2026-09-30 source-study-notes continuation adds HUMAN publication and the source-page entrypoint; the separate chat-generation tool remains deferred.
- [Minimal study memory implementation](logs/2026-08-14-1649--cardine--minimal-study-memory--log.md) — private record/search tools for topics covered and attributable learner signals, prompt-private canonical storage, and completed-capability settlement.
- [Learner Model Context Map contract](notes/2026-08-14-1446--cardine--learner-model-context-map-contract--note.md) — recovered target behavior for attributable study evidence, multidimensional mastery, exam-relative readiness, coverage/confidence, and the strict separation between factual ledgers and derived estimates.
- [Repeated deictic flashcard live failure](logs/2026-08-14-1438--cardine--repeated-deictic-flashcard-live-failure--log.md) — the clean-path lesson-scope fix misses a repeated failed `questa lezione` request, falls back to the 3,676-chunk global planner, and misreports the local planning failure as provider unavailability.
- [Deictic flashcard lesson-scope fix](logs/2026-08-14-0142--cardine--deictic-flashcard-lesson-scope--log.md) and [live diagnosis](logs/2026-08-14-0129--cardine--live-flashcard-lesson-scope-failure--log.md) — `questa lezione` now reuses the nearest recent uniquely resolved lesson and avoids the 3,676-chunk global planner failure.
- [Conversation-memory handoff](handoffs/2026-08-14-0044--cardine--conversation-memory-tools--handoff.md), [implementation log](logs/2026-08-14-0044--cardine--conversation-memory-tools--log.md), and [ADR-0002](decisions/2026-08-14--ADR-0002--conversation-memory-is-context-not-evidence.md) — bounded search/read over older canonical turns, source-only flashcard grounding, v3 excerpt-safe retry handoffs, and post-routing structural trajectories.
- [Canonical source viewer handoff](handoffs/2026-08-13-2344--cardine--canonical-source-viewer--handoff.md), [implementation log](logs/2026-08-13-2344--cardine--canonical-source-viewer--log.md), and [layout follow-up](logs/2026-08-14-0006--cardine--source-viewer-layout--log.md) — lateral Sources-page PDF/Markdown reading pane and bounded resizable citation viewer over authenticated canonical revisions; awaiting shared-worktree integration and live restart.
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
- Generated lesson materials likewise remain one atomic complete/study proposal
  batch; no output becomes a canonical source before explicit HUMAN decisions
  and dependency-aware publication.
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
