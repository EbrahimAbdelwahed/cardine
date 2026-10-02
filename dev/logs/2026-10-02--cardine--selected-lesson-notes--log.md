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
corrected and its focused regression passes. Final committed-tree full suite
and clean-archive custody verification are pending.

Desktop/mobile synthetic captures live outside the repository at
`/private/tmp/cardine-selected-notes-visual`. Fresh screenshot critique prompted
readable body-font headings and larger checkbox affordances. Initial mobile
capture caught the viewport transition; final captures wait for it to finish.
No model/provider call, deployment or merge performed. Chat note generation
remains deferred. Publication, current-head CI and automatic review are pending.
