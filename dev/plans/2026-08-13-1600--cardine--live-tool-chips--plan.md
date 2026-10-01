# Plan: live Tool Chips in chat

Date: 2026-08-13 16:00 CEST
Area: Cardine / tutor diagnostics / chat UI

## Goal

Show a compact, truthful activity disclosure under the assistant message while a tutor turn is running, using only events observed by Cardine. Keep the first delivery process-local and intentionally non-persistent across reloads.

## Scope

- In scope: remove the legacy session activity block; bounded in-memory activity store; authenticated lock-free polling endpoint; retrieval, capability, verification, and harness-tool observations; settled receipt records; Tool Chips renderer and browser polling.
- Out of scope: replay after reload, durable canonical events, playbook trace reconstruction, SSE/WebSocket, source drawer, changes to the opaque tutor trace.

## Public seams

1. `TurnActivityStore.snapshot(request_id)` and its module-level observation helpers.
2. `GET /api/v1/turns/<request_id>/activity`, behind existing API authentication and independent of the repository mutation lock.
3. `/api/v1/session/turns` receipt field `activity_records`.
4. Pure `CardineAI.toolChips(options)` HTML renderer.
5. Browser journey from optimistic assistant message to settled assistant message.

## Approach

1. Restore the existing design-system gate by replacing the native citation `title` attribute.
2. Add store and endpoint through red-green slices, enforcing the closed vocabulary and data policy.
3. Observe real tutor operations at Cardine-owned adapters without changing study-agent interfaces.
4. Add the renderer, scoped styles, polling, and settled-message wiring; delete the contradictory legacy block.
5. Verify focused unit/integration/browser contracts, syntax, lint, security invariants, and a live turn if credentials permit.

## Risks

- The polling route must never acquire the long repository mutation lock.
- Polling must stay single-flight and silent on transient errors.
- Records must never contain learner/model text, search queries, tool arguments, canonical identifiers, or quoted evidence.
- Existing Claude-authored pin and source-chip work must remain intact.

## Verification

- `.venv/bin/python -m pytest tests/unit/demo tests/unit/cli -q`
- `.venv/bin/python -m pytest tests/e2e/test_cardine_repository_browser_journey.py tests/integration/demo/TUT08 -q`
- `node --check src/cardine/demo/ai-primitives.js`
- `node --check src/cardine/demo/browser.js`
- `.venv/bin/python -m ruff check src/cardine`
- UI gate counts for `innerHTML`, native `title`, design tokens, and reduced motion.
