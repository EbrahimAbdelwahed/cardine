# ADR-0001: Process-local live tutor activity

Date: 2026-08-13
Status: Accepted

## Context

Tutor turns are blocking HTTP requests, while the learner needs truthful progress in the chat. The existing turn trace intentionally stores only a typed decision discriminator and declares that it captures no payloads. Extending it with UI activity would weaken that policy. A canonical event created only to animate the interface would also give presentation state inappropriate domain authority.

## Decision

Cardine owns a second, bounded in-memory `TurnActivityStore` beside `TurnTraceStore`.

- The current context binds both the application-owned store and request ID, preventing cross-instance contamination.
- Records use a closed Cardine vocabulary. Labels are compiled locally; targets may contain only bounded canonical lesson/section titles already shown elsewhere in the product.
- Prompts, answers, tool arguments, search queries, evidence text, fingerprints, and canonical source/revision/chunk identifiers are rejected from activity records.
- A validated `start_capability` may additionally select one capability-specific progress sentence from the closed host-owned template advertised in its schema. It is exposed only to the authenticated live pending turn and is never stored in the durable handoff, telemetry, or canonical history.
- A lock-independent, authenticated `GET /api/v1/turns/<request_id>/activity` route supports polling while the tutor POST holds the repository mutation lock.
- The settled POST receipt carries the same safe records for the final message. In the first delivery they do not survive reload or process restart.

## Consequences

- Live activity is observable without changing tutor, tool-manifest, or canonical event contracts.
- Unknown requests after restart degrade to an empty `unknown` response instead of an error.
- Reload reconstruction and any durable representation remain separate future decisions.

## Alternatives Considered

- Extend `TurnTraceStore`: rejected because its payload-free diagnostic policy is intentionally narrower.
- Add a canonical activity event: rejected because the data exists only for transient presentation.
- Add SSE or WebSocket transport: deferred because bounded polling satisfies the first usable path with a much smaller change.
