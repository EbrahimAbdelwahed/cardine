# Useful Jev routing and bounded clarification

Owner-requested repair after the 2026-10-05 live-session analysis (analysis
points 1, 2, 3 and 6; points 4, 5 and 7 explicitly excluded). Base: fetched
`origin/main` at `d81d10a` (PR #26). Checkout:
`.claude/worktrees/agent-confirmations-repo-visibility-50c7d8`, branch
`codex/jev-routing-calibration`. Decision record:
[ADR-0027](../../docs/decisions/ADR-0027--useful-jev-routing-and-bounded-clarification.md).

## Evidence (read-only, live store `cardine-wave-a-live`)

Sixteen tutor turns (events 432–463) started no capability except one
regex-selected explanation; five consecutive `learner_question` presentations
had no continuation. Of 76 stored routing receipts only 12 used Jev's candidate
(route top probability median 0.58 against 0.8/0.2). Every flashcard turn fell
back to the legacy model, which in ON cannot author the turn-bound scope
contract. One fallback failed with `protocol_error` and left no receipt. No
canonical event, configuration or study file was written.

## Implementation

- `hosts/routing.py`: one flat `route` judgement over `capability:<id>`,
  `tool:<name>`, `assistant_message`, `ask_learner` (when legal) and `stop`,
  with criteria from `prompts/tutor_decision_v1` guidance; tool threshold
  removed; ON fallback `propose_flashcards` rebound through Jev scope steps or
  ended with fixed guidance; receipts gain `error_code`, `legacy_failure`,
  `fallback_binding` and are recorded when the fallback fails.
- `hosts/clarification_state.py`: shared answered-clarification state;
  `decision_schema`/`validate_decision` make a second consecutive question
  illegal for every decision port; prompt 1.8.0 forbids confirming explicit
  requests.
- `adapters/judgement/jev.py`: closed 4xx codes (`jev_credentials_rejected`,
  `jev_credits_exhausted`, `jev_request_rejected`).
- `application/routing_calibration.py` and
  `scripts/report_routing_calibration.py`: read-only receipt report with
  what-if acceptance per route shape. On the live store the retired cascade
  would accept 20% at 0.8/0.2, 41% at 0.6/0.2 and 68% at 0.5/0.15.

## Calibration next step

Thresholds are unchanged configuration. After the owner restarts the local
instance on this branch, collect flat-route receipts from real turns, then run
the report with candidate thresholds and choose `route_probability` /
`route_margin` from the flat distribution. No paid provider call or live
quality claim is part of this change.
