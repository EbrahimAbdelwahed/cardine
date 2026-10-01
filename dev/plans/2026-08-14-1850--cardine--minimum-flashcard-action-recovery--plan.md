# Plan: minimum flashcard action recovery

Date: 2026-08-14 18:50 CEST
Area: Cardine / tutor orchestration

## Goal

Make the first explicit live request to generate lesson flashcards execute
`propose_flashcards` even when Luna returns a conversational promise. Preserve
visible progress without widening the decision protocol or making
`assistant_message` non-terminal.

## Scope

- In scope:
  - the four exact failed live formulations from events 183, 185, 187, and 189;
  - bounded Italian verb/card variants (`generi`, `fai ... cards`) and the
    observed `falshcards` transposition;
  - clause-local negation so `non generare flashcard` remains negative while
    `non mi rispondere, genera le cards` remains an action request;
  - one real repository/browser-turn regression where Luna deliberately returns
    the same promise observed live, but the host executes lesson-scoped
    `propose_flashcards` and publishes reviewable proposals;
  - preserve the existing host-owned Tool Chip and optimistic pending feedback.
- Out of scope:
  - adding `continue` to `assistant_message`;
  - adding a new decision kind, progress event, persistence schema, or replay
    version;
  - fuzzy spell-checking beyond the one observed transposition;
  - changing conversation-memory semantics or the general agent decision loop;
  - repairing unrelated historical stale/global-planner runs.

## Interface Decision

Keep the existing deep decision interface unchanged:

- `assistant_message` remains a terminal presentation;
- `start_capability` remains the only model decision that requests capability
  execution;
- the host-owned `FlashcardProfileRoutingTutorDecisionPort` remains responsible
  for converting an explicit flashcard action into `start_capability`, even when
  the delegated model returns a promise;
- the existing optimistic pending message and host-owned
  `capability.propose_flashcards` Tool Chip remain the non-terminal user
  feedback. They make no canonical success claim.

This is the minimum safe change because Cardine already displays
`Sto generando e verificando le proposte flashcard…` while the request is in
flight. A model-authored announcement would require changing decision codecs,
fingerprints, handoffs, strict provider schemas, and replay contracts without
adding necessary behavior for this incident.

## Approach

1. Add red tests before production edits.
   - Extend `tests/unit/hosts/test_flashcard_routing_natural_language.py` with
     the exact four live messages.
   - Assert each becomes `StartCapabilityDecision("propose_flashcards", ...)`
     even when the delegate returns the observed assistant-message promise.
   - Add negative controls for `non generare flashcard`, flashcard meta
     questions, and unrelated uses of `fai`.
2. Narrowly repair intent recognition in
   `src/cardine/hosts/flashcard_routing.py`.
   - Add only the required Italian morphology and observed typo.
   - Replace the 32-character blanket negation scan with action-local negation
     detection that cannot cross a clause boundary.
   - Keep capability advertisement, profile clarification, conversation reads,
     and attached-lesson precedence unchanged.
3. Prove the real product effect in
   `tests/integration/demo/TUT08/test_flashcard_proposals.py`.
   - Use a multi-lesson fixture.
   - Make the decision model return the live promise for
     `voglio che generi 15 flashcards sulla lezione 1 di biochimica unificato`.
   - Assert the host overrides it, resolves only Lezione 1, invokes the
     flashcard generation model once, returns `status=completed`, and creates at
     least one reviewable proposed revision.
   - Assert no assistant promise is persisted as the selected terminal answer.
4. Restart the local live server from the verified worktree and replay the
   first failed live request in a fresh session. Confirm a capability run and a
   proposal revision exist before declaring the fix shipped.
5. Record the exact verification and live evidence in a focused `dev/logs/`
   entry, then commit and push the verified Cardine hotfix to the current
   release branch before restarting the live process from that revision.

## Acceptance Criteria

- The exact event-183 request produces a completed `propose_flashcards` run and
  reviewable proposals, not an assistant promise.
- All four live formulations cross the host routing seam as flashcard actions.
- `non generare flashcard` and existing meta-question controls do not generate
  cards.
- The generated prompt is scoped to Lezione 1 and excludes Lezione 2 in the
  multi-lesson regression.
- `AssistantMessageDecision`, its codec, fingerprints, provider schema, and
  terminal runner semantics are unchanged.
- The pending browser message is visibly non-terminal and never claims the
  cards were successfully created before settlement.

## Risks

- Broadening `fai` carelessly could route general questions to generation;
  negative controls are mandatory.
- Fixing only the unit matcher could hide a later lesson-scope failure; the
  repository-level proposal assertion is the release gate.
- Browser presentation remains non-authoritative; the Python host remains the
  sole execution authority.
- The live process was offline during diagnosis. A fresh-session live replay is
  required because process-local activity traces cannot be reconstructed from
  canonical events.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/hosts/test_flashcard_routing_natural_language.py`
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_flashcard_proposals.py`
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/unit/adapters/model/test_tutor_decision.py tests/integration/test_tutor_host_runner.py tests/integration/test_tutor_completion_handoff.py`
- `.venv/bin/ruff check src/cardine/hosts/flashcard_routing.py tests/unit/hosts/test_flashcard_routing_natural_language.py tests/integration/demo/TUT08/test_flashcard_proposals.py`
- `git diff --check`
- Live: health 200, replay the exact event-183 wording in a fresh session, then
  verify one new `propose_flashcards` run and at least one proposed flashcard
  revision.
