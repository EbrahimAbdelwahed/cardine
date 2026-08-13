# Plan: decision-only tutor diagnostics and stop contract

Date: 2026-08-02 15:39 CEST
Area: Cardine tutor decision boundary

## Goal

Make the diagnostic surface show only the validated model decision, remove the
ambiguous `stop(needs_learner_input)` decision in favor of `ask_learner`, and
prevent ordinary learner turns from silently completing without a tutor
presentation. Propagate generic host-contract changes to Study Agent Harness
where that repository owns the contract.

## Scope

- In scope:
  - replace the multi-phase advanced turn trace with a bounded decision-only record;
  - expose decision kind and, only for `stop`, its closed reason;
  - remove `TutorStopReason.NEEDS_LEARNER_INPUT` from the closed contract;
  - clarify the Cardine tutor system prompt's decision policy;
  - add regressions for greetings, ambiguous learner goals, and stop behavior;
  - mirror Harness-owned contract/schema changes in the public Harness repository.
- Out of scope:
  - storing prompts, learner text, model output bodies, sources, or credentials in diagnostics;
  - changing grounded capability behavior or source retrieval;
  - synchronizing Cardine and Harness as a live dependency.

## Approach

1. Reduce the Cardine trace store and browser diagnostic view to one decision
   record per correlated turn.
2. Fix decision classification so every closed decision kind is represented
   exactly and `stop` includes its closed reason.
3. Remove `needs_learner_input` from `TutorStopReason`; use `AskLearnerDecision`
   for clarification.
4. Tighten the Cardine system prompt so ordinary learner messages must resolve
   to a presentation-producing decision and `stop` is reserved for explicit,
   safe terminal conditions.
5. Add focused contract, adapter, application, HTTP, and browser tests.
6. Apply the generic enum/schema/runner contract change to the maintained
   Harness surface after confirming its branch ownership.
7. Run focused tests first, then full repository gates and independent review.

## Risks

- Removing an enum value is a breaking serialized-contract change for clients
  that emit `stop(needs_learner_input)`.
- A decision-only trace must retain correlation without retaining user or model
  payloads.
- Cardine and Harness are independent repositories after the split; shared
  history does not imply automatic propagation.
- The active preview clone and the migration checkout had diverged; their
  commits must remain integrated without discarding either line of work.

## Verification

- Focused tutor decision contract and adapter tests.
- Conversation application tests proving `ask_learner` persists a presentation
  and disallowed stop reasons fail validation before host execution.
- Repository-backed HTTP/browser test proving diagnostics display only the
  decision kind/reason and no phase list.
- Ruff, strict mypy, full pytest suite, and `git diff --check` in each modified
  repository.
