# Log: decision-only diagnostics and chat stop contract

Date: 2026-08-02 16:07
Area: Cardine tutor chat

## Summary

Reduced preview diagnostics to the validated tutor decision, removed
`needs_learner_input` from the stop-reason contract, excluded every stop branch
from Cardine's normal provider schema, clarified the tutor prompt, and made a
completed learner turn without a tutor presentation fail explicitly.

## Files Changed

- `src/study_agent/diagnostics/`: decision-only in-memory diagnostic store.
- `src/study_agent/adapters/model/tutor_decision.py`: chat-specific provider schema and validated-decision recording.
- `src/study_agent/prompts/tutor_decision_v1.py`: prompt version 1.1.0 and explicit chat behavior.
- `src/study_agent/hosts/`: shared stop-reason and runner mapping update.
- `src/study_agent/application/conversation_turn.py`: completed-without-presentation guard.
- `src/study_agent/demo/`, `src/study_agent/cli/`, and retrieval/grounding adapters: removed phase-event instrumentation and simplified diagnostics UI.
- `src/study_agent/hosts/source_grounding.py`: diagnostics keep the model decision, and pending continuations bypass source-intent rewriting.
- Tutor adapter, host contract, application, repository chat, and browser journey tests: regression coverage.

## Verification

- `.venv/bin/python -m pytest -q tests/unit/adapters/model/test_tutor_decision.py tests/unit/hosts/test_tutor_host_contracts.py tests/integration/test_conversation_turn_application.py tests/integration/demo/TUT08/test_repository_backed_chat.py tests/unit/demo/test_browser.py`: 88 passed, 2 skipped because local sockets are unavailable.
- `.venv/bin/ruff check src tests`: passed.
- `.venv/bin/mypy src/study_agent/diagnostics/turn_trace.py src/study_agent/adapters/model/tutor_decision.py`: passed.
- `.venv/bin/python -m pytest -q`: 2131 passed, 28 skipped, 8 failed. Four failures require local socket binding, which the sandbox forbids. The other four failures were reproduced unchanged at baseline commit `bca0dd4` in untouched playbook/design-system tests.
- `.venv/bin/mypy src`: the changed diagnostics and tutor adapter are clean; four pre-existing errors remain in `playbooks/engine.py` and `demo/ui_application.py`.
- `git diff --check`: passed.
- Independent privacy review: removed residual HTTP `entries` from the raw diagnostics endpoint; redacted stderr transport logging remains separate.
- Independent semantic review: fixed model-decision overwriting and preserved the last validated decision through idempotent retries.

## Notes

- `TutorHostRunStatus.NEEDS_LEARNER_INPUT` remains intentionally: it is the runtime status produced by `ask_learner`, not a stop reason.
- The generic harness still supports explicit `stop(completed|no_safe_action)`; only Cardine's ordinary provider-facing chat schema excludes stop.
