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
explanation's exact source locators, linked to the nearest non-generation human
turn, including retries after failed flashcard attempts. Missing, retired or
replaced evidence fails conservatively instead of selecting the whole course or
an older explanation. An explicit lesson pin retains its existing precedence.
Generic whole-source requests retain their existing behavior.

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

Exact product bytes are bound by `tests/parity/tutor-context-overlay.json`, after
the earlier implementation overlays. Historical copied-core approval remains
unchanged. Offline regression tests demonstrate current-topic correctness and
bounded planning, not provider latency or model quality.
