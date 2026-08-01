# Task Bead: TUT-08A durable adaptive conversation

Status: Done — implementation, independent tests, review, and approved fixes green
Priority: P0
Type: product tracer-bullet
Depends On: TUT-06, ADR-0015

## Outcome

One repository-backed application command records a learner turn, runs a
provider-neutral `TutorHostRunner`, persists a validated tutor presentation or
pending continuation, survives repository restart, resumes a continuation, and
returns exact retries without duplicate host work or canonical events.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- FP-01 Adaptive chat direct messages, questions, suspension, response, reload,
  and exact retry are canonical.
- Foundation for FP-02 and FP-10.

## Grilling Evidence

- Session/artifact:
  - `docs/decisions/ADR-0015--persist-validated-host-presentations.md`
  - architect approval recorded in the Cardine implementation session
- Decision state: approved
- ADR/glossary changes: ADR-0015; none additional

## Worker Profile

reuse `cardine-product-slice`

Rationale:

The work is a vertical repository/application/UI contract slice, but its
canonical behavior is fully fixed by ADR-0015.

## Context

The repository already contains `TutorHostRunner`,
`TutorPresentationReceipt`, `TutorPresentationRecord`,
`session.tutor_presentation_recorded@1`, and
`SessionTurnService.record_tutor_presentation`. It lacks a durable
`TutorContinuationStore` adapter and the one application owner that coordinates
learner recording, host execution, presentation commit, snapshot refresh, and
resume.

## What To Do

- Add a durable SQLite `TutorContinuationStore` with repository identity,
  no-follow, bounded-byte, exact-retry, and restart behavior matching existing
  adapters.
- Add `ConversationTurnApplication` with typed command/result/error contracts.
- Derive separate domain-separated learner, host-turn, and presentation
  idempotency identities from the server-owned request ID.
- Resolve an existing learner/presentation before stale-sequence rejection.
- Reject a new stale command before host/model work.
- Run `TutorHostRunner` and persist only validated direct-message,
  learner-question, or continuation-request receipts.
- Resume by opaque pending fingerprint; mark resolved continuations inactive
  without deleting canonical presentation history.
- Return `TutorSnapshotV1`, canonical tutor presentations, pending descriptor,
  and final high-water sequence.
- Compose the application and continuation adapter in `LocalRepository` through
  explicit injected host dependencies; preserve callers that use only
  grounding.
- Add restart, lost-response retry, changed-content, stale, race, missing-store,
  failed/interrupted host, suspension, and resume integration tests.

## Likely Files / Packages

- `src/study_agent/application/conversation_turn.py`: orchestration owner.
- `src/study_agent/application/__init__.py`: public application exports.
- `src/study_agent/adapters/sqlite/tutor_continuations.py`: durable adapter.
- `src/study_agent/adapters/sqlite/__init__.py`: adapter export.
- `src/study_agent/cli/repository.py`: optional host composition.
- `src/study_agent/sessions/turn_service.py`: only if a narrow resolution
  contract approved by ADR-0015 is missing.
- `tests/unit/application/`: command/retry tests.
- `tests/integration/`: restart/race/continuation tests.
- `tests/architecture/`: ownership and transport-boundary checks.

## Acceptance Criteria

- [x] Direct assistant messages and learner questions survive a fresh
  `LocalRepository.open`.
- [x] A suspended continuation survives restart and exact resume.
- [x] A resolved continuation is not returned as pending after reload.
- [x] Exact lost-response retry returns the committed presentation without a
  second host invocation or canonical event.
- [x] Same request identity with changed learner content conflicts.
- [x] New stale requests perform no host/model work.
- [x] Races commit no forged presentation and return a retryable conflict.
- [x] Failed, invalid, interrupted, or exhausted host results commit no
  successful presentation.
- [x] Old repositories and `TutorSnapshotV1` replay unchanged.

## Verification

- `PYTHONPATH=src:. python -m pytest -q tests/unit/application tests/integration/test_conversation_turn_application.py tests/integration/test_tutor_host_runner.py tests/unit/sessions`
- `PYTHONPATH=src:. python -m ruff check src/study_agent/application src/study_agent/adapters/sqlite src/study_agent/cli/repository.py tests/unit/application tests/integration/test_conversation_turn_application.py`
- `PYTHONPATH=src:. python -m mypy src tests` when available
- `git diff --check`

## Out Of Scope

- DeepSeek/OpenAI adapter selection.
- Artifact, assessment, recall, context, source, or plan UI routes.
- Capability output scraping into arbitrary tutor text.
- Hosting/authentication.

## Notes / Handoff

- Do not modify `TutorSnapshotV1` or verified assistant-turn provenance.
- Completed with scoped request identities, command-bound results, explicit
  shared continuation-store composition, and resolved-continuation deletion.
- Verification: 107 focused tests passed; Ruff and `git diff --check` passed.
