# Current topic and context settlement

Status: owner-requested repair, 2026-10-04.

Jev routing and the selected payload receive the recent canonical conversation
(eight entries, 4,000 characters) and bounded same-turn tool observations
(four entries, 8,000 bytes). Future or unsequenced entries are excluded. Context
is untrusted conversational data, never evidence or execution authority. Only
the chosen payload schema is advertised; fingerprints and other action authority
are excluded. The runner continues to validate every resulting decision.

Flashcard requests with explicit topics retrieve at most eight current canonical
chunks before planning. The learner's current explicit topic takes precedence
over model-distilled inputs. References to the latest explanation reuse that
explanation's exact source locators, selected structurally from its completion handoff, including retries after failed
flashcard attempts. Missing, retired or
replaced evidence fails conservatively instead of selecting the whole course or
an older explanation. An explicit lesson pin retains its existing precedence.
Requests without a resolved scope ask the learner or fail conservatively.

The selected content view retains whole historical ChunkIds, offsets and page
provenance, masking unselected text without changing offsets. Existing planner,
classification and worker checks remain responsible for canonical integrity.
Recovery remains attached to historical canonical worker commitments. Generated
cards still require explicit human acceptance.

An integrity-valid explanation with insufficient evidence settles as terminated
with the existing localized evidence-limit message. It cannot publish an answered
explanation or supported claims. Unknown handles, stale citations and conflicting
evidence still fail the existing validators. Answered explanations validate once.
The copied Harness validators and answered-only manifest remain unchanged.

Native SSE closes HTTP resources and its diagnostic span before exposing DONE.
Normal iterator closure after DONE no longer creates a false provider failure;
cancellation and actual stream errors retain their existing failure behavior.

Full source identity for recent explanations is recovered through the original
completed handoff, checking manifest, authority, retry identity and output hash.
Canonical citations are re-resolved before selecting current chunks; display
locators never authorize selection. Presentations without a completion handoff cannot authorize flashcard evidence.

Canonical locator bounds in both copied planner and prepared scope expand from
2,000 to 16,000 characters. Both use one shared bound, retain full exact locators
and reject larger values. Derived topic titles stay within 1,000 characters;
canonical metadata, ChunkIds, offsets, citations and hashes are unchanged. This
is a bounded copied-core repair, not an installed-package migration.

Exact continuation bytes are bound by `tests/parity/tutor-context-overlay.json`, after
the earlier implementation overlays. Historical copied-core approval remains
unchanged. Offline regression tests demonstrate current-topic correctness and
bounded planning, not provider latency or model quality.


## Closed Jev flashcard scope (2026-10-04 continuation)

Tutor ON replaces the duplicate language interpretation of action, topic,
references and profile with Jev Choice decisions. Its closed scope is
`explicit_topic`, `latest_explanation`, `selected_lesson`, `conversation`, or
`ambiguous`. Ambiguity asks the learner. The selected profile is another closed
Choice; composition consumes it without scanning query words again.

The host serializes the exact `FlashcardScope` contract in the string `scope`
field. Free-form scope strings are not accepted.
It fixes this field in the selected payload schema and binds it to the actual
learner interaction ID and text hash. The model cannot select a canonical ID.
The query is bounded to 512 characters, and Jev checks that an explicit topic,
lesson name or conversation topic follows the selected context rather than an
invented or superseded topic. This is a probabilistic semantic check; canonical
source verification remains deterministic.

The gateway resolves a selected lesson from current canonical candidates, not
request regexes. An explicit pin is validated and intersects topic/explanation
selection; a selected-lesson request uses only that pin. The latest explanation
is found through structural completion handoffs, including after failed card
requests, and its exact original citations are re-resolved. Missing/stale scope,
replaced evidence, or an empty intersection fails conservatively. Conversation
summaries provide retrieval context only and cannot become cited evidence.

The duplicate action/topic/reference/profile language interpreter is removed.
Tutor flashcards require the semantic ON route; OFF/SHADOW do not advertise the
capability. Direct selected-lesson generation builds the same closed contract
from its validated pin and explicit profile selection. The explicit emergency
decision fallback is called at most once and cannot bypass the scope contract.
Cancellation never enters fallback. Choice distributions must cover all options,
be finite and normalized, and pass configured probability/margin thresholds.

The continuation overlay binds only the owner-authorized Cardine changes;
previous copied-core approvals and bytes remain untouched. No retrieval
adjacency expansion, index optimization, post-generation grounding, model spend,
study-store migration or rollout belongs to this outcome.
