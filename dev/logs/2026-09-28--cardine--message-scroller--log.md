# Message Scroller and BeUI chat component evaluation

Owner requested BeUI Message Scroller in chat and an assessment of Citations and
Message Bar (the referenced site's Prompt Input). Implementation ownership:
`/Users/ebrahimabdelwahed/Desktop/Dev/cardine`, `codex/message-scroller`, based on
PR #5's `codex/cardine-wave-a-recovery` at `08ea435`. This stacked PR depends on
#5; its base must expose only this outcome, not the recovery lineage.

Implemented the BeUI interaction pattern in Cardine's existing JS/CSS shell:
message navigation with bounded previews/current indicator, following at the
live edge, interruption for history reading, return-to-latest, asynchronous
content resize/mutation handling and observer cleanup. Fixed outer-container
scrolling found during browser verification by clipping the scroller frame and
the conversation-owned view root. No React dependency or third-party source
transplant. Canonical writes, provider calls and credentials are unaffected.

The owner request authorizes this product UI evolution. The recovery overlay
updates only the browser.js and browser.css hashes for this patch; the origin
checkpoint and other rows are retained. Existing uncommitted flashcard repairs
and their pending custody decision are preserved and excluded from this PR.

Assessment and recommendations: [BeUI chat component note](../notes/2026-09-28--cardine--beui-chat-components--note.md).
Citations should use canonical identity for deduplication and local numbered
viewer rows in a later scoped change. Inline markers require explicit claim
mapping. Keep the current composer until more real actions/model choices or
server execution cancellation justify BeUI Prompt Input features.

Verification so far: behavior fixture and focused tests pass; HTTP contracts
run with loopback permission; Ruff and mypy pass. Real browser checked at
1280×720 and 390×844 with 20 synthetic messages: jump to first message, return to
end, bounded rail, no horizontal mobile overflow and outer scroll remains zero.
No live model calls or user study data used. Screenshot is a temporary preview
artifact, not committed design source.

Full prescribed verification and PR/CI handoff are recorded below at delivery.
