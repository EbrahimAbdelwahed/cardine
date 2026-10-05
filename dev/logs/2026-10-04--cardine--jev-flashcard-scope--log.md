# Closed Jev flashcard scope

Owner-requested task points 1–2, one independently verifiable outcome.
Base: fetched `origin/main` at `6e9c904` (PR #22). Dedicated checkout:
`/Users/ebrahimabdelwahed/.codex/worktrees/jev-flashcard-scope/cardine`;
branch `codex/jev-flashcard-scope`. The shared project checkout and its dirty
files were preserved. Implementation commit: `962df76`. PR: https://github.com/EbrahimAbdelwahed/cardine/pull/26.
Delivery uses one PR on this branch; current-head CI and automatic GitHub review
remain required before any merge.

## Implementation and authority

Jev selects a closed flashcard scope and profile; narrow generation receives
only the selected capability schema with the host-fixed scope contract. Jev also
checks topic/lesson/conversation query binding against the current request.
The gateway validates the learner ID and text hash, resolves selected lessons
from current canonical candidates, and resolves the most recent explanation
through its original handoff and exact citations. The typo `quesot` is covered
on the real repository/UI route with a deterministic fake Jev; no claim about
live provider accuracy or paid-call latency is made.

An explicit pin remains authoritative and bounds topic/explanation selection.
Missing/stale contracts and empty canonical evidence fail conservatively.
Conversation context is never evidence. The October 5 no-compatibility instruction
removed the action, stopword, deictic, lesson-reference and profile interpreters
and their unused helpers. OFF/SHADOW do not advertise tutor flashcards. Explicit
emergency routing may run once but cannot bypass the structured scope requirement.
Cancellation and weak/malformed/incomplete distributions retain existing host
validation. Cards still require HUMAN acceptance and enrollment.

## Integration with parallel owners

No files in `knowledge/document_index.py`, `unitizer.py`,
`flashcards/semantic.py` or `application/study_semantics.py` changed.
`application/flashcard_proposals.py` has two narrow integration points:
`start` consumes `semantic_flashcard_profile` from `FlashcardScope`, and
`_request` gives the worker a readable query rather than the closed JSON
scope transport. Preserve these when integrating the separate post-generation
grounding owner. The canonical planner, citations, revision commitments,
source stores, consent and server credentials retain their original owners.

The current `tutor-context-overlay.json` is refreshed for these owner-authorized
Cardine bytes, adding the closed contract and profile binding. Historical
copied-core hashes are unchanged. ADR-0025 records the durable contract.

## Verification and delivery

Offline regression coverage includes typo/latest explanation, current explicit
topic, conversation context and canonical selected lesson, with and without a
pin and a preceding failed request. It covers ambiguous scope/profile, complete
finite normalized distributions, weak thresholds, cancellation, exactly-one
fallback, missing/stale/wrong-turn scope and separation of long-chat prose from
canonical evidence. The fixture suite contains 42 flashcard integration cases
plus the long-conversation regression.

Ruff, mypy (679 files), ownership audit (322 rows), wheel/sdist build and archive
verification, and provider-free installed-wheel imports pass. Final full suite: 2,998 passed, 4 skipped in 194.73 seconds. Earlier runs exposed the now-corrected free-form scope
fixture and an intermittent student-journal concurrent append failure in code
and tests identical to the base; no unrelated journal code was changed.

No merge, deployment, paid provider call, study-store migration or local rollout
was performed. No unpublished changes exist in the shared checkout from this task.

2026-10-05 merge preparation: first-review findings reproduced and fixed.
Pinned retrieval sends canonical whole-chunk bounds into FTS before ranking
limits, including title and bounded relevance branches and query commitments.
Contextual flashcard labels no longer append factual topic entries. Existing
explanation memory stays authoritative. Regression tests fail before the fixes.
Current exact-byte overlay binds repository composition; post-baseline hashes
bind only the two changed retrieval files. Historical manifests are preserved.

Integrated PR #23: preserve both closed-scope and grounding configuration,
checkpoint recovery guard, and all profile/gate regressions. The fixture had
overwritten the resolved grounding model with the default routing config;
preserving explicit judgement configuration fixes four failing integration
cases. All 96 combined scope/gate/pin tests pass. No production workaround.
