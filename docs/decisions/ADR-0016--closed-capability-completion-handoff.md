# ADR-0016: Closed capability-completion handoff for product orchestration

Date: 2026-07-30
Status: Accepted

## Context

`TutorHostRunner` can complete a capability through the authority-bound
gateway, but its product-facing result retains only generic completed output.
It does not retain the closed capability/run identity required to recover a
verified owner receipt. ADR-0015 correctly prohibits copying arbitrary
capability JSON into tutor speech or canonical conversation state.

Cardine must let the tutor generate grounded artifact proposals and invoke
other supported capability-owned effects without weakening proof,
authorization, idempotency, or source-grounding contracts.

Artifact acceptance may also make an accepted flashcard eligible for recall.
Acceptance and recall enrollment belong to different existing owners and
cannot be presented as one atomic event-store transaction.

## Decision

- Extend host completion additively with a closed
  `TutorCapabilityCompletionReference` containing exactly:
  - capability identity and version;
  - capability manifest fingerprint;
  - canonical run ID;
  - verified output fingerprint;
  - the gateway retry-receipt fingerprint.
- Only `TutorHostRunner`, after a completed gateway result passes its existing
  authority, identity, lifecycle, and retry checks, may construct the
  reference.
- Generic completed output remains operational data. It is never tutor speech,
  an artifact proposal, a grade, or another canonical effect by itself.
- Add a private product `CapabilityCompletionHandlerRegistry`. Each handler is
  closed to one capability identity/version and must recover the referenced
  verified output through the capability's existing owner adapter before
  calling an existing canonical application service.
- Unknown, unsupported, mismatched, or unrecoverable completions remain
  status-only. They create no successful presentation or canonical product
  effect.
- A handler returns a bounded typed product receipt naming the canonical IDs it
  committed. The receipt may inform UI refresh; it is not copied into the
  session timeline.
- Generated artifacts remain proposals and never self-accept.
- If Cardine elects to enroll an accepted flashcard in recall, artifact
  acceptance commits first through `ArtifactService`. Recall enrollment is a
  separate resumable application step with a deterministic, domain-separated
  service request identity derived from the accepted revision.
- Exact recovery checks the accepted artifact and enrollment separately.
  Failure to enroll never rolls back or conceals the accepted artifact.
- A later exact retry resumes the missing enrollment without duplicating the
  artifact decision or schedule.

## Consequences

- Cardine can route supported completed capabilities to existing proof owners
  without scraping raw output.
- The exact seven StudyTools and existing capability schemas remain unchanged.
- Product orchestration gains an additive private registry and typed receipts.
- Acceptance-to-enrollment is explicitly eventually consistent and
  restart-recoverable rather than falsely atomic.
- A capability may complete successfully while Cardine has no product handler;
  this is honest status, not a forged user-visible result.

## Alternatives Considered

- Render `completed_output` as tutor text: rejected because it launders
  unclosed JSON into canonical conversation.
- Let the HTTP route interpret capability output: rejected because transport
  would own domain behavior and proof recovery.
- Add product-specific branches to the generic gateway: rejected because the
  gateway must remain capability-neutral.
- Atomically accept an artifact and enroll recall in one event batch: rejected
  because the commands have distinct authority, validation, retry identities,
  and owners.
- Auto-accept generated artifacts: rejected because it violates the proposal
  lifecycle and human-decision boundary.
