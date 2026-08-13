# Handoff: live Tool Chips

Date: 2026-08-13 16:47 CEST
Area: Cardine / tutor diagnostics / chat UI

## Current State

Cardine now shows a compact expandable activity column under the assistant message while a tutor turn runs and after it settles. Every row comes from an observed Cardine event; no prompt, query, tool argument, evidence text, or canonical source identifier is exposed.

## Completed

- Process-local bounded `TurnActivityStore` and accepted ADR.
- Authenticated polling endpoint independent of the long tutor mutation lock.
- Real capability, retrieval, harness invocation, and verification observations.
- Browser polling with race recovery, navigation cancellation, failure backoff, and attempt cap.
- Final record correlation by `presentation_id`, including flashcard chat turns.
- Legacy session “Attività” section removed; “Stato tutor” preserved.
- Pinned chat flashcard scope fix from the parallel Claude lane preserved and tested.

## Remaining

- Delivery two only after real use: reconstruct activity after reload from in-memory records/traces. Do not add a canonical event solely for UI persistence.
- A known browser journey still fails on base and current branch before model invocation; diagnose separately if prioritized.
- The known PDF AnyDoc worker integration failure remains unrelated.

## Important Context

- `unknown` must remain retryable while the matching turn is pending; otherwise polling can lose the GET-before-capture race.
- Harness tool activity begins only inside the actual gateway invocation, never from the model’s decision alone.
- Activity observation must never abort the underlying tutor operation.
- The browser map is keyed by `presentation_id`; never regress to one global “last activity” slot.
- Recovery bundles in the repository root remain untracked.

## Verification

- Unit demo/CLI/diagnostics: 186 passed.
- Focused product slice: 59 passed.
- Authenticated lock-independent HTTP route: 3 passed outside sandbox.
- Ruff, Node syntax, design-system gates, diff check, visual critique, and semantic review: passed.
