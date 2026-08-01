# Plan: Repository-backed Cardine chat tracer

Date: 2026-07-30 00:00
Area: product-shell
Status: Completed

## Goal

Let the polished Cardine browser run one real, restart-safe chat flow through
the existing local repository composition and grounded-answer owner. A learner
must be able to submit a message, receive a canonical answer, reload the page,
and see the same course/session timeline reconstructed from the event stream.

## Scope

- In scope:
  - A transport-independent repository-backed UI application.
  - Explicit repository, course, and session selection at server startup.
  - Canonical grounded-answer execution with request-ID idempotency.
  - Real `TutorSnapshotV1` session and material DTOs.
  - Honest unavailable states for features not included in this tracer.
  - Localhost-only CLI composition and deterministic real-repository tests.
  - A temporary personal repository configured for DeepSeek so the flow can be
    tried in the browser.
- Out of scope:
  - Remote hosting, authentication, tenancy, or public mutation.
  - Artifact, assessment, recall, and context-resolution commands.
  - A second tutor loop or browser-owned persistence.
  - Changes to prompts, model policy, or canonical domain contracts.

## Approach

1. Confirm the existing canonical answer path and snapshot representation.
2. Add `RepositoryUiApplication`, reopening the explicit local repository per
   request and delegating the mutation to `GroundingAskService`.
3. Allow `BrowserSurface` and the localhost server to receive that application;
   add explicit `--repository`, `--course-id`, and `--session-id` arguments.
4. Map canonical snapshot/material records to the existing bounded UI DTOs.
5. Add unit, HTTP, idempotency, stale-sequence, and reload tests using a
   deterministic model adapter over a temporary real repository.
6. Run focused gates, semantic review, and a live DeepSeek browser smoke.

## Risks

- Model/provider latency happens inside a request thread; the first tracer is
  intentionally synchronous and bounded.
- The public demo and repository mode must remain isolated.
- A stale browser sequence must fail before a model call.
- Credential values must remain environment-only and never enter DTOs or logs.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/demo tests/integration/demo/TUT08 tests/e2e/test_cardine_browser_contract.py tests/unit/adapters/model/test_openai_compatible.py tests/unit/cli/test_repository.py tests/integration/test_grounding_ask_service.py tests/unit/sessions`
  passed: 153 tests.
- Focused repository-backed tests cover exact retries, changed-content
  conflicts, stale requests before model execution, post-model CAS races,
  concurrent commands, localhost origin checks, and reload.
- Ruff and `git diff --check` passed.
- Mypy was unavailable in the environment.
- The live DeepSeek browser flow accepted Enter, returned a grounded answer,
  advanced to sequence 25, and restored the same answer after reload without
  console warnings.
