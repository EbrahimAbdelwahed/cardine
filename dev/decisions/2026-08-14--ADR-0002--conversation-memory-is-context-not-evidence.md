# ADR-0002: Conversation memory is context, not evidence

Date: 2026-08-14
Status: Accepted

## Context

The tutor decision prompt receives only a recent bounded conversation window.
Long-running study sessions still need older learner and assistant turns to
recover discussed topics, difficulties, and preferences, especially before
flashcard generation. Sending the complete transcript on every turn would undo
the latency improvement, while using previous assistant prose as medical
evidence could propagate an earlier model error.

## Decision

Expose older messages through two private, read-only operations:
`conversation.search` and `conversation.read`. Both are restricted to the
current course/session and the tutor snapshot high-water sequence captured for
the decision step. Returned excerpts are bounded, same-turn untrusted data.

Conversation memory may select and prioritize a flashcard scope, but card facts
remain grounded only in canonical source chunks. A lesson pin remains the
strongest scope constraint.

Raw recovered excerpts and arbitrary model summaries are removed before a
durable completion handoff. The handoff retains the final bounded capability
query/scope and a host-normalized topic-term sketch because an issued action
must be exactly retryable after process loss. Structural trajectories retain
only post-routing decision kinds, operation names, capability IDs, and opaque
correlation; they never retain arguments or content.

## Consequences

- Long chats remain recoverable without increasing every prompt.
- Retrieval can iterate within the existing four-decision host budget.
- Prior conversation cannot prove a medical claim or bypass canonical evidence.
- Exact capability retry remains possible without persisting retrieved message
  excerpts.
- The structural trace is useful for routing diagnostics, but a future semantic
  training export still requires explicit consent and a separate privacy design.

## Alternatives Considered

- Always include the complete transcript: rejected for latency and token cost.
- Give Luna filesystem or `grep` access: rejected as an unnecessarily broad
  authority surface.
- Treat conversation as flashcard evidence: rejected because assistant prose is
  not canonical truth.
- Persist raw excerpts for retry or training: rejected for privacy and boundedness.
- Add embeddings, a classifier, or an agent SDK now: rejected until lexical
  memory retrieval produces measured failures.
