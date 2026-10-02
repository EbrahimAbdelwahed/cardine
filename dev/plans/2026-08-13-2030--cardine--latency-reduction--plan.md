# Plan: targeted latency reduction

Date: 2026-08-13 20:30 CEST
Area: Cardine / runtime / retrieval / chat UI

## Goal

Remove the measured latency that grows with source size and conversation length
without changing canonical events, citations, public response DTOs, or adding a
new runtime dependency.

## Scope

- In scope: coherent persisted-projection snapshot fast path; immediate chat
  receipt rendering; bounded recent decision context; bounded FTS integrity
  validation and canonical result lookup; lock-independent read routes where
  coherence can still fail closed; focused history bounding if isolated.
- Out of scope: provider streaming, generic agent redesign, new persistence,
  background task framework, source rechunking, or changes to canonical data.

## Approach

1. Prove snapshot DTO equivalence while preventing replay when the event store
   exposes a coherent persisted projection.
2. Render a settled chat receipt before the advisory bootstrap refresh.
3. Give the decision model a deterministic, interleaved recent conversation
   tail instead of the unbounded full history.
4. Stop rebuilding and rescanning the canonical retrieval catalog per result;
   retain fail-closed integrity at index-version boundaries.
5. Let coherent GET reads proceed independently of the long mutation/model
   lock, then bound initial session history if the change stays local.

## Risks

- A projection observed at a different high-water sequence must never be paired
  with the captured events; fall back to replay or retry.
- Retrieval optimization must preserve canonical citation resolution and detect
  stale/tampered derived indexes.
- Context compaction must retain the latest tutor question and learner answer.
- Read concurrency must not weaken optimistic sequence checks for mutations.

## Verification

- Focused snapshot, retrieval, repository UI, browser, routing and grounding
  tests.
- Exact DTO equality between replay and persisted-projection snapshot paths.
- Call-count regressions instead of wall-clock assertions.
- Ruff, Node syntax, diff check, then the relevant broader Cardine slice.
