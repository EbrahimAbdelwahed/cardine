# Selected PDF lesson notes

Owner approved implementation on 2026-10-02 after the repository-backed design
proposal in this chat. Checkout: `68e2/cardine`; branch:
`codex/selected-lesson-notes`; fetched base: `origin/main` at `9465dae`.

PDF boundary editing and selection are now distinct UI steps. The selection
starts empty, offers select-all, retains edited ranges when returning to the
editor, displays the selected lesson count and sends only checked ranges.
The browser retains a failed submission's request ID until selection changes.

`selected_lessons` is an explicit alternative to the legacy full-coverage
`lessons` API. The server validates current parent revision, page bounds,
nonempty titles, disjoint ranges, size, consent and capacity before extraction.
It sorts selected ranges and binds each job's request identity to parent
source/revision plus title/range. Nonconsecutive selections, reordered retries
and partial-batch recovery after reopening reuse the existing coordinator.
Canonical extraction manifests, independent proposal pairs, human decisions
and parent-lifetime publication checks remain unchanged.

The new exact four-path custody overlay supersedes existing byte bindings for
this approved continuation. Historical recovery, study-note and student-journal
overlays are preserved. The audit tests cover missing/foreign bindings and
source/digest tampering. This does not claim installed-Harness parity.

Verification so far: 15 selective-generation integration cases, the two offline
browser journeys, 13 design-system checks, Ruff, mypy, JS syntax, ownership audit
and isolated sdist/wheel build pass. The first full suite had 2,864 passes, four
optional/live skips and one new CSS spacing-token failure; the spacing token is
corrected and its focused regression passes. Final committed-tree verification: **2,869 passed, four skips** (optional PDF
containment and three disabled live-provider tests). All ten clean-archive audit
tests pass, including the new selective-note custody test. The final wheel
contains exact copies of all four changed source/UI files.

Desktop/mobile synthetic captures live outside the repository at
`/private/tmp/cardine-selected-notes-visual`. Fresh screenshot critique prompted
readable body-font headings and larger checkbox affordances. Initial mobile
capture caught the viewport transition; final captures wait for it to finish.
Fresh critique of the final full captures and crops reports no visible defects.
No model/provider call, deployment or merge performed. Chat note generation
remains deferred. Implementation checkpoint: `1069831`. Published PR:
https://github.com/EbrahimAbdelwahed/cardine/pull/14 (base `main`, no unmerged dependency).
Final documentation is delivered in the same branch/PR. Current-head CI and
automatic GitHub review must be checked on the submitted head; no manual review
request has been posted and no review round is claimed complete. The PR will be
marked ready after this documentation is pushed. No remaining local source work.
