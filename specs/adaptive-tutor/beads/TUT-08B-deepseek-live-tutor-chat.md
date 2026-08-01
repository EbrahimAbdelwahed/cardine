# Task Bead: TUT-08B DeepSeek live tutor chat

Status: Done
Priority: P0
Type: product tracer-bullet
Depends On: TUT-08A

## Outcome

The existing Cardine chat runs the full closed tutor-decision loop through a
DeepSeek adapter, renders direct messages/questions/continuations from canonical
state, and resumes them from the browser after reload.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- FP-01 Adaptive chat is live in the browser.
- FP-02 DeepSeek owns natural-language behavior.
- FP-10 Chat and continuation E2E states.

## Grilling Evidence

- Session/artifact:
  - `specs/adaptive-tutor/cardine-full-product.md`
  - `docs/decisions/ADR-0015--persist-validated-host-presentations.md`
- Decision state: approved
- ADR/glossary changes: none; provider remains a technical adapter

## Worker Profile

reuse `cardine-product-slice`

Rationale:

This is the provider-backed browser tracer over the TUT-08A application
contract; no domain redesign is permitted.

## Context

Cardine currently calls `GroundingAskService` directly. The repository has a
generic OpenAI-compatible model adapter for playbooks, while `TutorHostRunner`
requires a closed `TutorDecisionPort`. The product text-processing policy
requires DeepSeek and forbids provider calls from UI components.

## What To Do

- Implement or adapt a DeepSeek/OpenAI-compatible `TutorDecisionPort` using the
  existing closed `TutorDecision` schema and bounded JSON-object transport.
- Version the tutor-decision prompt and add deterministic/provider eval
  fixtures.
- Compose `TutorHostRunner`, context assembler, capability gateway, authority,
  interruption, identity, and durable continuation store inside the repository
  application composition.
- Replace repository-backed `/session/turns` grounding-only execution with
  `ConversationTurnApplication`; retain grounded ask as a capability/tool.
- Implement continuation response POST routing and safe error mapping.
- Join canonical tutor presentations and pending descriptor into the private
  session DTO without changing `TutorSnapshotV1`.
- Render assistant message, learner question, suspension, retry, and restored
  continuation in the existing chat UI.
- Preserve public-demo behavior and localhost same-origin protection.

## Likely Files / Packages

- `src/study_agent/adapters/model/`: DeepSeek tutor-decision adapter.
- `src/study_agent/prompts/`: versioned decision prompt if required.
- `src/study_agent/cli/repository.py`: full tutor composition.
- `src/study_agent/demo/ui_application.py`: conversation command binding/DTO.
- `src/study_agent/demo/browser.py`: parameter composition only.
- `src/study_agent/demo/browser.js`: continuation rendering/command details.
- focused unit, integration, eval, and Cardine E2E tests.

## Acceptance Criteria

- [x] A real DeepSeek-backed direct tutor response is committed and restored
  after reload.
- [x] A clarification produces a canonical pending continuation; browser
  response resumes it after process/repository restart.
- [x] Provider output outside the closed schema is rejected without canonical
  success.
- [x] New stale commands invoke neither host nor provider.
- [x] Exact retry does not invoke the provider twice after canonical commit.
- [x] Provider keys remain environment-only and are absent from DTOs/logs.
- [x] Enter/Shift+Enter/IME behavior remains green.

## Verification

- focused adapter and conversation tests with recorded responses
- opt-in live DeepSeek smoke against the personal repository
- Cardine submit, suspend, reload, resume, and console inspection
- Ruff, mypy when available, and `git diff --check`

## Out Of Scope

- Artifact/assessment/recall UI activation.
- New tutor-decision kinds.
- Hosting/authentication.

## Notes / Handoff

- Use `json_object` for DeepSeek structured output and validate the closed
  decision locally.
- Recorded fixtures, restart, continuation, HTTP, retry, stale, and redaction
  gates are green. A live smoke using only the packaged public fixture returned
  a canonical learner question and restored the joined timeline.
