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
six-path context overlay, preserving historical core approval.
Automatic GitHub review and current-head CI are required delivery evidence;
this request does not authorize merging or deployment.
