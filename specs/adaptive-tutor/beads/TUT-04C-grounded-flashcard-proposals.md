# Task Bead: TUT-04C grounded flashcard proposal capabilities

Status: Done
Priority: P0
Type: expand
Depends On: TUT-04A, TUT-03

## Outcome

`propose_flashcards@1` generates a bounded, source-grounded proposal batch using
one trusted pedagogical profile selected by the host.

## Child Beads

- [TUT-04C0 — shared flashcard batch and trusted dispatch](TUT-04C0-flashcard-batch-and-dispatch.md)
- [TUT-04C0A — lesson generation planning](TUT-04C0A-lesson-generation-planning.md)
- [TUT-04C0B — isolated bundle worker execution](TUT-04C0B-isolated-bundle-workers.md)
  - [TUT-04C0B1 — isolated worker primitive](TUT-04C0B1-isolated-worker-primitive.md)
  - [TUT-04C0B1A — gateway-worker proof adapter](TUT-04C0B1A-gateway-worker-proof-adapter.md)
  - [TUT-04C0B2 — lesson worker fan-out](TUT-04C0B2-lesson-worker-fanout.md)
- [TUT-04C1 — hybrid macro-detail implementation](TUT-04C1-hybrid-flashcard-profile.md)
- [TUT-04C2 — morphology-first anatomy implementation](TUT-04C2-morphology-flashcard-profile.md)
- [TUT-04C3 — profile gateway and adversarial evals](TUT-04C3-flashcard-profile-evals.md)

## Acceptance Criteria

- [x] One trusted lesson scope is indexed before generation and divided into
  ordered, non-overlapping coherent topic/paragraph bundles. Every worker sees
  the compact global index but can ground facts only in its active bundle.
- [x] Every bundle executes in a fresh isolated worker context; interruption
  resumes from durable operational receipts without contaminating the tutor
  conversation or replaying successful workers.
- [x] Hybrid generation follows parsimony, emits framework before earned details,
  resolves parent linkage, and rejects overlap, duplicates, and unsupported
  claims. The 24-card bound is per worker/page only, never a lesson target.
- [x] Morphology profile clusters anatomical objects/regions, validates bounded
  reconstruction plus earned discriminations, keeps contextual deletion
  selective, and rejects unverified media or spatial claims.
- [x] Shared content is exporter-neutral: no deck, Anki tags, raw HTML, provider
  selector, credential, or live Anki operation.
- [x] Prompt layers treat source, examples, continuation, and candidate keys as
  untrusted data; validators derive canonical-safe batch output and provenance.
- [x] Capability has empty state-write policy and cannot accept proposals.

## Verification

- Planner/bundle/worker recovery contracts, profile-specific prompt/validator
  fixtures, direct gateway evals, injection, source gaps, parsimony/parent
  overlap, tool parity, and full gates.

## Pinned topic retrieval and memory

Pinned topic retrieval applies the canonical lesson span before ranking limits,
including exact-title and bounded relevance paths. Contextual scope labels are
transport data and must not be recorded as factual topics in the student journal.

## Grounding policy recovery

When documentary grounding is ON, completion recovery must reject a checkpoint
without the current grounding-policy fingerprint before registering proposals.
Already recorded proposals remain readable. OFF and SHADOW retain their recovery behavior.
