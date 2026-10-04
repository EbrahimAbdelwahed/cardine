# Flashcard context and settlement repair

Owner request: repair missing/old-topic responses, verification errors and slow
flashcard generation in the local app at port 8765.

Checkout: Codex managed worktree `1603/cardine`, branch
`codex/flashcard-context-reliability`, based on fetched main `1ebf70a`.

Read-only local diagnostics showed flashcard planning failing before a generation
call, source-bound uncertainty rejected by the answered-only capability validator,
and normal SSE completion incorrectly recorded as a provider failure. Jev ON
received the latest utterance without recent conversation or tool observations.
No live provider request, source mutation or restart was performed.

Offline regressions reproduced large-course planning and absent selected-payload
context before the repair. Current implementation bounds topic planning, resolves
deictic requests against the newest explanation's canonical evidence, keeps
conversation available through tool/payload routing, settles integrity-valid
uncertainty truthfully and closes diagnostic spans before DONE.

The live server still runs another checkout at main `1ebf70a`. These changes are
not active there. PageIndex lesson picking already exists in PR #18; study-note
progress is a separate continuation, not part of this reliability patch.

Validation: full offline suite 2,907 passed, four optional skips; Ruff and strict
mypy (670 files) passed; isolated wheel/sdist build and 322-row custody audit
passed. Additional context-boundary coverage was checked after the full suite. Exact product bytes use the new
eight-path context overlay, preserving historical core approval.
Automatic GitHub review and current-head CI are required delivery evidence;
this request does not authorize merging or deployment.


First automatic review on `d2de6b9`: two actionable P2 findings (English deictic
article and truncated display locator). Both were reproduced offline and repaired
in this same branch. Full explanation evidence now recovers through its verified
completion handoff and is freshly re-resolved against current canonical chunks.
The regression revealed a second 2,000-character locator ceiling in the planner
and prepared scope. Both share a bounded 16,000-character limit; topic presentation
titles are clipped to their existing limit without modifying source metadata or
canonical evidence. The two copied-core paths are explicitly bound in the new
continuation overlay, leaving historical manifests intact.

Tool observation byte limits now include omission markers and JSON overhead.
Italian/English and short/long-title requests complete at the real application
seam. First round fixes are followed by only one final automatic review; no third
round is authorized. PR #21 separately publishes note progress on PR #18.

Post-review full suite: 2,913 passed, four optional/live skips; Ruff, strict mypy,
isolated wheel/sdist build and 322-row custody audit passed. Completion recovery
also matches the canonical presentation context/action fingerprints and permits
its fresh settlement sequence without widening the original execution authority.

Owner continuation, 2026-10-04: “termina il lavoro” explicitly authorizes
completion after the review limit, without requesting another semantic review.
Two remaining findings reproduced: supported action/politeness words prevented
deictic resolution, and requests over 512 characters failed RetrievalQuery.
Topic extraction now removes the same supported actions recognized by routing,
filters polite request words and fits the retrieval contract on token boundaries
(a single oversized token remains bounded and cannot become empty/global scope).
Italian/English explicit and deictic application regressions plus all supported
action forms cover this continuation. Original capability input identity is intact.

The retired-source finding was disproved at the real repository seam.
_RepositorySourceCatalog.documents excludes retired IDs before both indexing
and the search integrity audit; _ensure_retrieval_index compares that filtered
catalog fingerprint. A regression gives ten soon-retired chunks all first eight
results, retires the source with an old index still present, then verifies one
active-only generated proposal. No redundant retrieval/core change was needed.

An overlapping manual follow-up caused a third duplicate automatic review on the
previous head; this was acknowledged to the owner. No further review is requested.
All known findings are now either repaired with executable reproduction or
dismissed with production-path regression evidence. Current-head CI and explicit
merge authorization remain required; port 8765 still runs the old checkout.

Final owner-continuation verification: 2,924 offline tests passed with four
expected optional/live skips (clean-archive cases run separately after commit).
All 63 focused topic/routing cases pass, including both new deictic regressions,
large-course explicit polite requests, long queries and the retirement test.
Ruff, strict mypy (670 files), isolated wheel/sdist build and the 322-row custody
audit pass. Final submitted-head CI is checked after publication. No unpublished
runtime changes are intended, and no additional review or rollout is requested.
