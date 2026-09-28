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

Delivery verification:
- Full offline suite after the design-token correction: 2491 passed, 4 skipped
  (opt-in provider smokes and optional PDF worker). Existing unrelated local
  flashcard repairs are present in that working-tree run but excluded from PR.
- Final focused behavior/design checks: 14 passed; HTTP/browser checks: 31 passed.
- Ruff and mypy pass (621 files); isolated wheel/sdist and artifact verification
  also pass from a clean archive of the submitted code, excluding local repairs.
- Browser refresh keeps both history position and draft; desktop and mobile
  transcript/composer stay within bounds and outer scroll remains zero.
- Added regression protection for a stale initial `scrollend` arriving during
  a smooth rail jump: it cannot resume following until the jump reaches its
  clamped target. This final correction is undergoing full-suite verification.

Published PR: https://github.com/EbrahimAbdelwahed/cardine/pull/6, attached to
this chat. Base remains `codex/cardine-wave-a-recovery` (PR #5 dependency).
CI and automatic Codex semantic review must be assessed on the final submitted
commit before any owner-authorized merge. This task does not authorize merging.
Unrelated flashcard code/tests, their log and dev/index entry, and the transfer
log remain local and unstaged.
