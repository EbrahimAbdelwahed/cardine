# ADR-0015: Persist validated host presentations under the session owner

Date: 2026-07-29
Status: Accepted

## Context

The reference product UI must survive reloads and exact request retries without
placing tutor conversation state in browser or HTTP-server memory.

`SessionTurnService.record_assistant_turn(...)` already persists verified
`tutor_message@1` playbook output. ADR-0005 deliberately rejects arbitrary
host-declared text as proof of a successful assistant turn. The later
`TutorHostRunner`, however, also emits bounded direct messages, learner
questions, and suspended-continuation requests after it has assembled a
sequence-consistent context and schema-validated a closed tutor decision.
Those presentation-only results currently carry too little provenance to
become canonical and disappear on reload.

Treating a validated host presentation as a verified playbook output would
invent a run and weaken the meaning of `AssistantTurnRecord`. Keeping it in UI
memory would create a second, non-replayable conversation.

## Decision

- Keep `AssistantTurnRecord`, `session.assistant_turn_recorded@1`, and
  `TutorSnapshotV1` unchanged.
- Add a closed `TutorPresentationReceipt` produced only by
  `TutorHostRunner` after decision validation. It contains:
  - a stable host-turn identity;
  - presentation kind (`assistant_message`, `learner_question`, or
    `continuation_request`);
  - bounded learner-visible content;
  - the observed host-context sequence and fingerprint;
  - a domain-separated decision/receipt fingerprint;
  - for continuations only, the opaque continuation fingerprint, capability
    identity, and bounded response schema metadata.
- Add `TutorPresentationRecord` and the additive
  `session.tutor_presentation_recorded@1` event under the existing session
  owner.
- Add `SessionTurnService.record_tutor_presentation(...)`. It accepts only a
  `TutorPresentationReceipt`, requires service authority, verifies exact
  session/reply ownership, commits with compare-and-swap at the sequence
  observed by the host, and resolves an exact retry before rejecting a stale
  sequence.
- Add a projection-backed presentation view. The private UI session DTO joins
  its rows with the unchanged `TutorSnapshotV1`; it does not alter the public
  snapshot contract.
- Add a durable repository-backed `TutorContinuationStore`. Operational
  continuation records remain opaque and separate from canonical presentation
  events, but their safe descriptors are discoverable through canonical
  presentation records after restart.
- Add one `ConversationTurnApplication` orchestration owner. For a request it:
  1. derives domain-separated learner, host-turn, and presentation identities
     from the server-owned request identity;
  2. records the learner turn through `SessionTurnService`;
  3. returns an already committed presentation before invoking the host;
  4. otherwise runs or resumes `TutorHostRunner`;
  5. records only direct message, question, or suspended-continuation
     presentations carrying a valid receipt;
  6. returns fresh canonical views.
- Capability completion remains owned by its existing gateway/application
  service. Arbitrary completed capability output is never scraped into tutor
  text.
- Failed, interrupted, cancelled, stopped, terminated, or budget-exhausted host
  work does not create a successful presentation record.
- The HTTP handler only validates transport input and calls
  `ConversationTurnApplication`; it never appends events or opens SQLite.

## Retry and conflict semantics

- Repeating the same request identity and learner content returns the existing
  learner turn and presentation without another canonical event or host call.
- Reusing a request identity for different content or a different presentation
  is an idempotency conflict.
- A new request with a stale browser sequence performs no host/model work and
  returns a retryable conflict.
- The presentation append uses the sequence observed by the host, not another
  browser-provided sequence.
- A missing operational continuation is rendered degraded and unresumable; it
  is never silently recreated as canonical success.
- Resolving a continuation must make its prior pending presentation inactive
  on reload.

## Consequences

- Direct tutor messages, questions, and continuation prompts become replayable
  without weakening verified playbook provenance.
- Old event streams and `TutorSnapshotV1` replay unchanged.
- The session package remains the only canonical conversation owner.
- Exactly-once canonical effects are provided. Exactly-once provider invocation
  across a crash between host execution and presentation commit is not claimed;
  that would require a separate durable command journal.
- The new event, projection, receipt, continuation adapter, and orchestration
  contract require focused replay, restart, race, idempotency, and bounded-data
  tests before the HTTP mutation route is enabled.

## Alternatives Considered

- Append session events in the HTTP handler: rejected because it duplicates
  domain ownership.
- Store presentations in browser or server memory: rejected because reload and
  restart lose canonical conversation.
- Reuse NOTE interactions: rejected because it misclassifies tutor speech.
- Create a synthetic playbook run or fake `RunId`: rejected because it invents
  provenance.
- Relax `AssistantTurnRecord.output`: rejected because it breaks ADR-0005.
- Persist generic capability output: rejected because unclosed JSON is not a
  learner-visible presentation contract.
- Change `TutorSnapshotV1` immediately: rejected because a private UI join is
  sufficient and preserves the public contract.
