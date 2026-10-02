# Compact Thinking and Streaming Text

## Ownership and base

- Owner: this UI task, branch `codex/activty-thinking-states`.
- Isolated checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/thinking-stream/cardine`.
- Explicit user-selected base: `origin/codex/cardine-wave-a-recovery`,
  `08ea43553289f28891b2de72eae5742f6765d275` (dependency PR #5).
- The shared `codex/message-scroller` checkout and its uncommitted work were
  preserved. The separate unpublished recovery commit was not imported.

## Accepted presentation and behavior

Beautiful UI 02 Thinking and 03 Streaming Text provide the approved visual
reference. The chat has no demo variant selectors or extra reasoning card.
`retrieval.lesson` uses a bare monospace source title (Coding), source searches
use the Search result treatment, and other observations use compact Steps with
inline rounded tags. All labels and arguments consume the existing safe
activity DTO; raw tool arguments and search queries remain excluded.

Activity sequences key live rows so polling updates states and appends new
observations. Running traces start expanded; completion collapses the trace,
and later refreshes preserve the reader's disclosure choice. Stale polling
responses cannot update a newer turn or a different route.

The existing transport publishes a complete verified answer. Word appearance
begins immediately upon its receipt; this is client presentation streaming,
not provider token streaming. Markdown structure and verified citations remain
intact. History does not replay the animation. Navigation clears timers, and
reduced motion shows the answer immediately. Scroll follows only when the
reader was at the end.

Copy preserves readable paragraphs. Retry creates a fresh turn/request key
and retains the original submitted lesson scope; it is available for normal
turns whose command is still known in this browser session. Historical turns
loaded after a browser reload and consumed continuations have Retry disabled
rather than reconstructing an unknown command. Follow-ups populate the draft.
Like and Dislike are mutually exclusive, reversible browser-session choices;
there is no rating endpoint or canonical study evidence write.

A closed source-viewer dialog was painting outside the chat and creating body
scroll. A shared closed-dialog display guard fixes that observed layout defect.
MIT attribution for the reference icons and presentation is packaged alongside
the existing icon licenses.

## Verification

- Full offline suite: 2,489 passed, 4 optional tests skipped.
- Focused demo suite covers renderer dispatch, escaped content, sequence keys,
  streaming completion/cleanup/reduced motion, idempotent bindings, copying,
  feedback, scoped Retry, completion collapse, and stale polling.
- Ruff, mypy, JavaScript syntax checks, diff checks, wheel and sdist build.
- Real browser preview on port 8767 used only process-local simulated DTOs,
  without provider calls, credentials or canonical study stores. Confirmed
  compact geometry, 24px actions, 12.5px follow-ups, collapsed completion,
  Copy, exclusive feedback, follow-up draft population, and a fresh Retry turn.
- Delivery remains a dependent PR; no merge or deployment is authorized.
