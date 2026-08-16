# Plan: bounded clarification recovery

Date: 2026-08-13 20:10 CEST
Area: Cardine / tutor routing

## Goal

Prevent an answered tutor clarification from starting another circular
clarification turn, without adding a classifier or a generic agent loop.

## Scope

- In scope:
  - prompt guidance that treats the learner's answer to the latest tutor
    question as resolved context;
  - one semantic decision retry when Luna still emits `ask_learner`;
  - integration coverage through `LocalRepository.tutor_conversation`.
- Out of scope:
  - retrieval, capability implementations, UI, persistence schema, or a
    general multi-step agent runtime;
  - broad conversation-history redesign.

## Approach

1. Reproduce the live pattern through the public conversation seam.
2. Add a small decision-port wrapper that detects an immediately preceding
   learner question, supplies only that question and the current answer as a
   recovery hint, and asks Luna for one final decision.
3. Accept the second validated decision even when genuine ambiguity remains;
   never retry more than once.
4. Version the tutor decision prompt with explicit positive and negative
   follow-up examples.

## Risks

- A genuinely unclear reply may still require a second question. The retry is
  semantic, not a forced capability route, so Luna may retain `ask_learner`.
- Recovery context must contain no provider payloads, source text, or trusted
  authority fields.

## Verification

- Focused red/green integration test for the exact live dialogue shape.
- Existing routing, semantic retrieval recovery, lesson pin, and repository
  chat tests.
- Ruff, mypy where configured, and `git diff --check`.
