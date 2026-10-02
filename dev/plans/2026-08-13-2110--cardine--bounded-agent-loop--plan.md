# Plan: bounded agent loop

Date: 2026-08-13 21:10 CEST
Area: Cardine / tutor runtime

## Goal

Allow Luna to observe a harness-tool result and choose the next action in the
same tutor turn, without introducing a generic agent framework or an unbounded
loop.

## Scope

- In scope: compact tool observations, same-turn re-decision, exact-call
  deduplication, prompt guidance, public conversation regressions.
- Out of scope: new tools, a classifier, autonomous mutations outside the
  existing manifests, UI Tool Chips changes, and changes to lexical recovery.

## Approach

1. Pin the behavior at `LocalRepository.tutor_conversation` through the browser
   application boundary: a tool call must be followed by a model-authored final
   response that can see the tool result.
2. Extend the existing bounded `TutorHostRunner` loop with ephemeral, redacted
   tool observations and exact invocation fingerprints.
3. Refuse duplicate execution, expose that refusal as an observation, and let
   Luna select a different action within the existing four-decision budget.
4. Version and clarify the decision prompt, then run focused and broader host
   verification.

## Risks

- Tool results may be large; observations must be recursively bounded before
  entering model context.
- Canonical writes must not be repeated after a model retry; existing tool
  idempotency remains authoritative and exact duplicates are not executed.
- The loop must terminate through the existing decision budget even when Luna
  repeatedly chooses unusable actions.

## Verification

- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_repository_backed_chat.py -k agent`
- `PYTHONPATH=.:src .venv/bin/python -m pytest -q tests/integration/demo/TUT08/test_semantic_retrieval_recovery.py tests/integration/demo/TUT08/test_routing_recovery.py tests/unit/hosts tests/unit/adapters/model/test_tutor_decision.py`
- `.venv/bin/python -m ruff check <changed Python files>`
- `git diff --check`
