# Useful Jev routing and bounded clarification

Status: owner-requested repair, 2026-10-05.

## Context

A live session (2026-10-05, 16 tutor turns) produced five consecutive
confirmation questions, a flashcard written as chat prose instead of a proposal,
and no started capability except one regex-selected explanation. Routing
receipts showed why: across 76 stored receipts Jev's top route probability had
median 0.58, so only 12 candidates passed the configured 0.8/0.2 route policy.
Jev scored five abstract route labels ("Start an advertised capability") and then
a second, separately thresholded capability or tool choice; uncertainty between
`start_capability` and `invoke_tool` alone defeated the threshold. Every rejected
turn fell back to the legacy model, which in ON cannot construct the turn-bound
flashcard scope contract, so it could only ask or talk. Learner questions carry no
continuation, so each "sì" was routed from scratch. Failed legacy fallbacks left
no routing receipt and provider failures recorded no code.

## Decision

1. **One flat action choice.** Outside a pending continuation, Jev scores one
   closed set of concrete actions: each advertised capability, each advertised
   tool, `assistant_message`, `ask_learner` (when legal) and `stop`. Option keys
   are `capability:<id>`, `tool:<name>` or the bare route. Each option carries the
   same versioned positive/negative routing guidance that the legacy prompt uses
   as its Jev criterion. The route policy threshold applies to this single choice;
   the separate tool threshold is removed. Capability thresholds still govern the
   flashcard scope, profile and topic-binding sub-choices.
2. **At most one clarification in a row.** When the newest learner message
   answers the newest tutor `learner_question` and no continuation is pending,
   `ask_learner` is not a legal decision: `decision_schema` omits it and
   `validate_decision` rejects it for every decision port. This is host lifecycle
   narrowing, not language interpretation. Jev receives the answered question as
   `answered_tutor_question`, so a confirmation binds the action that question
   proposed. Where the host would otherwise ask a fixed flashcard clarification
   in this state, it answers with fixed guidance instead.
3. **No silent dead end in ON fallback.** When the emergency fallback selects
   `propose_flashcards` in ON, the router discards the fallback inputs and runs
   only Jev's existing scope, profile, query and topic-binding steps. The legacy
   model chooses the route but can never author the scope contract. If those
   steps do not resolve, the turn ends with fixed host guidance rather than a
   fabricated proposal or another question.
4. **Durable failure telemetry.** A routing receipt is recorded even when the
   fallback itself fails (`legacy_failure`), judgement failures carry a closed
   `error_code` (Jev adapter codes, model error codes or `unclassified`), and the
   fallback flashcard binding outcome is recorded. Jev distinguishes rejected
   requests (`jev_request_rejected`) from transient failures. Turn traces stay
   memory-only per ADR-0024; receipts remain derived, content-free telemetry.
5. **Calibration from receipts.** `scripts/report_routing_calibration.py` reads
   a repository's receipts read-only and reports acceptance, fallback reasons,
   top-probability quantiles and what-if acceptance for candidate thresholds.
   Thresholds remain explicit configuration; this repair does not change any
   repository's configured values.

## Consequences

The route policy now measures confidence in the concrete action. A learner who
answers "non ho capito" receives an assistant message instead of a second
question. Canonical scope, evidence, validators, idempotency and HUMAN proposal
decisions are unchanged. Offline tests use scripted judgements; no paid provider
call or live calibration result is implied.
