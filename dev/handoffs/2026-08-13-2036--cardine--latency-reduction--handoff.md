# Handoff: targeted latency reduction

Date: 2026-08-13 20:36 CEST
Area: Cardine / runtime / retrieval / chat UI

## Current State

Five targeted latency fixes are implemented and verified in the shared
`codex/cardine-wave-a-recovery` worktree. They are not yet committed, pushed,
or deployed by this side conversation. The durable live repository remains
`../cardine-wave-a-live` and was inspected read-only.

## Completed

- Coherent persisted-projection snapshot fast path.
- Immediate rendering of settled tutor receipts.
- Latest-24-entry decision context for Luna, with full canonical history kept.
- One canonical FTS catalog materialization per audited search.
- Lock-independent coherent UI GET reads while mutations remain serialized.
- Focused regression coverage and live before/after measurements.

## Remaining

- Review and publish the shared worktree changes from the main thread.
- Restart/redeploy the live server and measure end-to-end first-token and
  navigation latency in the browser.
- Treat old-message pagination as a separate small contract if the remaining
  99 KB session payload becomes material; do not silently truncate history.
- Separately repair durable Tool Chips activity replay across application
  restart. It is a pre-existing failing contract, not part of this latency diff.

## Important Context

- Snapshot speed comes from trusting the storage-owned projection only when its
  course and high-water sequence equal the captured event stream; mismatch
  falls back to canonical replay.
- The 24-entry bound applies only to model routing context, not the conversation
  shown to the user or canonical events.
- FTS still performs its fail-closed integrity audit. The optimization removes
  duplicate per-result catalog scans, not safety checks.
- Preserve the untracked recovery bundles in the repository root.

## Verification

- Focused behavior suite: 40 passed.
- Broader relevant suite: 221 passed, 2 skipped, with two sandbox/external-worker
  failures and one independently reproduced pre-existing Tool Chips restart
  failure.
- Ruff, Node syntax, and diff checks passed.
- The standard mypy command remains blocked by the repository's pre-existing
  duplicate namespace-package discovery; targeted analysis found no error in
  the changed snapshot or context modules.
