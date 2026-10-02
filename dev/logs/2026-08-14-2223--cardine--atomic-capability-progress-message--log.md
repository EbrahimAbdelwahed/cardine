# Log: Atomic capability progress message

Date: 2026-08-14 22:23 CEST
Area: Cardine tutor decision and live presentation

## Summary

Added one atomic, non-terminal progress hint to `StartCapabilityDecision` while
keeping `AssistantMessageDecision` terminal. Luna can select the exact safe
capability-specific sentence advertised by the host in the same structured
decision that starts the capability. The host still owns validation, authority,
idempotency, handoff persistence, execution, and final canonical presentation.

Security review narrowed the initial free-form design: progress copy is now a
closed host-owned template, excluded from the operational fingerprint and the
durable v3 completion handoff. It is published best-effort only after handoff
acquisition, exposed through the authenticated process-local activity polling
route, rendered with `textContent` in the optimistic pending bubble, and cleared
on settlement. It never becomes canonical chat history or telemetry.

The OpenAI Responses adapter now applies the same strict required-nullable schema
projection as the live model adapter and removes provider-introduced null optionals
before local decoding. Prompt `tutor_decision.v1` is version `1.7.0`.

## Files Changed

- `src/cardine/hosts/contracts.py`: atomic field, closed per-capability templates, schema/codec, presentation-neutral operational fingerprint.
- `src/cardine/hosts/runner.py`: post-handoff best-effort publication; v3 handoff remains progress-free.
- `src/cardine/adapters/host/openai_responses.py`: strict optional-field projection and null cleanup.
- `src/study_agent/prompts/tutor_decision_v1.py`: version 1.7.0 and atomic progress routing guidance.
- `src/cardine/diagnostics/turn_activity.py`: transient snapshot v2 field and settlement clearing.
- `src/cardine/demo/browser.js`: safe optimistic-bubble replacement via `textContent`.
- `src/cardine/hosts/flashcard_routing.py`, `src/cardine/hosts/source_grounding.py`: wrapper preservation and existing recovery compatibility.
- Focused contract, adapter, runner, handoff, diagnostics, browser and TUT08 tests.
- `dev/decisions/2026-08-13--ADR-0001--process-local-turn-activity.md`: approved transient template exception.

## Verification

- Contract/model/router/runner: 113 passed.
- Completion handoff: 11 passed.
- Flashcards, Tool Chips, bounded loop and routing recovery: 16 passed.
- OpenAI Responses and Luna adapters: 21 passed.
- Diagnostics and browser assets: 20 passed.
- Authenticated local HTTP activity route: 5 passed outside the socket sandbox.
- Ruff: passed.
- `node --check src/cardine/demo/browser.js`: passed.
- `git diff --check`: passed.
- Independent semantic re-review: no remaining HIGH/MEDIUM findings.
- Independent security/privacy re-review: no remaining HIGH/MEDIUM findings.
- Broader repository chat slice: 27 passed, with two documented unrelated failures (AnyDoc worker availability and pre-existing post-restart activity-record equality) plus two sandbox socket skips.

## Live Restart

- Supervised process restarted from PID `6881` to PID `66184`.
- `GET http://127.0.0.1:8765/health`: HTTP 200, runtime `cardine-local-source-grounding-v2`, mode `setup`.

## Notes

- Local-owner password and runtime OpenAI credential are intentionally in-memory and must be configured again after restart.
- Provider-backed live replay remains pending until the user configures the local credential; no secret was requested or persisted during this change.
