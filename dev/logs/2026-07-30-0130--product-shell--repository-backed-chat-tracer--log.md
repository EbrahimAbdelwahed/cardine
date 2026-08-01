# Log: Repository-backed Cardine chat tracer

Date: 2026-07-30 01:30
Area: product-shell

## Summary

Connected the polished Cardine browser to the existing canonical
`LocalRepository` grounded-answer path. The localhost server now opens an
explicit course/session, delegates chat turns to `GroundingAskService`, and
renders `TutorSnapshotV1` state after restart or reload.

The integration includes a canonical expected-sequence fence, request
idempotency, same-origin JSON mutation checks, and DeepSeek JSON-object
structured-output support. Features not included in this tracer remain
explicitly unavailable instead of being simulated in browser state.

## Files Changed

- `src/study_agent/demo/ui_application.py`: repository-backed application
  boundary and canonical UI DTO mapping.
- `src/study_agent/demo/browser.py`: explicit repository CLI composition,
  actual health mode, and guarded localhost mutations.
- `src/study_agent/application/grounding_ask.py`: optional authoritative
  expected-sequence fence before model execution.
- `src/study_agent/sessions/service.py`: CAS-bound grounded-answer finalization.
- `src/study_agent/cli/repository.py`: event-store injection and selectable
  structured-output transport.
- `src/study_agent/adapters/model/openai_compatible.py`: JSON-object response
  mode for compatible providers.
- `tests/integration/demo/TUT08/test_repository_backed_chat.py`: real repository,
  restart, retry, concurrency, HTTP, health, and origin coverage.
- `tests/integration/test_grounding_ask_service.py`: stale-before-model and
  race-during-model sequence-fence coverage.
- Focused unit and browser-contract tests were extended for the new transport
  and UI assets.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/demo tests/integration/demo/TUT08 tests/e2e/test_cardine_browser_contract.py tests/unit/adapters/model/test_openai_compatible.py tests/unit/cli/test_repository.py tests/integration/test_grounding_ask_service.py tests/unit/sessions`:
  153 passed.
- `PYTHONPATH=src:. python -m ruff check ...`: passed.
- `git diff --check`: passed.
- Live localhost browser: Enter submitted to DeepSeek, canonical sequence
  advanced from 22 to 25, reload restored the same final learner/tutor pair,
  and browser warning/error logs were empty.
- Mypy: unavailable (`No module named mypy`).

## Notes

- The live personal repository is `/private/tmp/cardine-live-repository`; its
  configuration names `DEEPSEEK_API_KEY` but stores no credential value.
- This first tracer intentionally exposes grounded chat, materials, evidence,
  and context conflicts. Host continuations, artifacts, assessments, recall,
  and exam planning still require their canonical application owners.
- The server remains localhost-only. Hosting and authentication are deferred.
