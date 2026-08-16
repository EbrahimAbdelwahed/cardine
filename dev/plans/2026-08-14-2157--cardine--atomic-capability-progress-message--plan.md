# Plan: Atomic capability progress message

Date: 2026-08-14 21:57 CEST
Area: Cardine tutor decision and live presentation

## Goal

Let Luna select one bounded, non-terminal host-owned progress message with a capability start
without weakening the host-owned state, authority, persistence, or execution
contract. Keep `assistant_message` terminal.

## Scope

- In scope:
  - optional `progress_message` on `StartCapabilityDecision` only;
  - strict schema, codec, fingerprint, provider projection, prompt, and wrapper preservation;
  - restart-safe capability handoff preservation;
  - best-effort process-local publication after handoff acquisition and before execution;
  - authenticated polling and escaped rendering in the optimistic pending bubble only;
  - compatibility for decisions and handoffs that predate the field;
  - retain the narrow flashcard recovery as a safety net.
- Out of scope:
  - `continue` on `assistant_message`;
  - progress messages on `InvokeToolDecision`;
  - canonical timeline persistence or domain events for transient progress;
  - SSE/WebSockets;
  - removing the flashcard recovery before live provider evidence supports it.

## Interface Decision

`StartCapabilityDecision(capability_id, inputs, progress_message=None)` is the
single atomic decision. When present, the message must equal the safe template
advertised for that exact capability. It grants no authority and never claims
success. Because it is presentation-only, the operational fingerprint excludes it.

The host persists only the operational start decision in the existing compatible
handoff format, then publishes the message best-effort to the process-local turn-activity channel.
The authenticated browser polling response may expose it only for the live pending
turn. It is never a `TutorPresentationRecord` and never survives as canonical chat
history. Publication failure cannot block execution.

## Approach

1. Extend the typed decision interface, strict schema, codec and provider projection.
2. Version the tutor prompt and teach Luna to use progressive, non-result language.
3. Preserve the field through source/flashcard wrappers and the durable capability handoff.
4. Extend the process-local activity snapshot and optimistic browser bubble, preserving escaping.
5. Add contract, model, runner, handoff, activity, browser, wrapper, and repository-turn tests.
6. Run focused and broader tutor suites, independent semantic/security review, then restart live.

## Invariants

- The harness owns state, authorization, idempotency and all effects.
- `assistant_message` remains terminal.
- Progress publication occurs only after decision validation and durable handoff acquisition.
- Progress is best-effort and cannot fail the capability.
- Old decisions without the field and handoff schemas v1-v3 remain readable; progress is not durable.
- No model text enters diagnostics traces or external telemetry.
- The browser escapes progress text and displays it only to the authenticated current session.

## Risks

- Model text may repeat private context; bound length, same-session authentication, no telemetry,
  transient storage, and prompt restrictions mitigate this.
- Past-tense language may imply success before settlement; prompt and tests prohibit it.
- Wrapper reconstruction can silently drop the field; focused preservation tests are required.
- The shared worktree is dirty; changes must preserve unrelated edits in overlapping files.

## Verification

- Focused contract/model/runner/handoff/activity/browser/wrapper tests.
- Repository-backed flashcard turn proving action and transient progress coexist.
- Existing tutor decision, bounded loop, completion handoff, Tool Chips, and flashcard suites.
- Ruff, focused mypy if configured, browser syntax/design gates, and `git diff --check`.
- Independent semantic and security review.
- Live restart and HTTP 200 health check; provider replay after local credentials are configured.
