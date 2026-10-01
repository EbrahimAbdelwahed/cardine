# Minimal student journal

Owner request: strip Cardine's unused learner-evidence logic and context conflicts;
keep an append-only student-state.json journal behind one UI/agent service, with
recall history separate. Work is isolated in the Codex-managed student-state
worktree on codex/student-state, based on PR #5 at 24cd4f7. No dirty files from
the original codex/notes-generation checkout were copied or changed.

Implemented: append-only JSON Lines file, locking/fsync, idempotent writes,
trusted agent origins and UI self-reports, recent tutor context/search, Percorso
read/write/import UI, factual assessment activity with actual grade score, safe
historical import and note redaction, removal of evidence projections/tools/API
and product context-conflict routes/composition. Recall remains separate. Old
SQLite history and context replay are preserved. Contract: specs/student-journal.

Validation on implementation commit 7841f85: full offline suite: 2543 passed,
4 skipped, 1 failed in 102.44s. The sole failure is the clean-archive ownership
audit rejecting intentionally changed frozen core bytes. Journal/tool/assessment
tests pass (23); browser/tool journeys pass (17); Ruff and strict mypy pass;
wheel/sdist build passes. PR: https://github.com/EbrahimAbdelwahed/cardine/pull/12
(draft). GitHub CI/review remains outstanding. This follow-up only records results.

Blocker: automatic approval review rejected changes to the ownership-audit policy
and a proposed new custody overlay, citing lack of explicit authority and possible
weakening of future integrity checks. The policy and all existing custody manifests
remain unchanged. The read-only audit correctly rejects changed frozen source
bytes and the retired evidence module. A proposed patch under dev/proposals binds
only 25 named source paths to exact SHA-256 values and declares the two deleted
paths; it has NOT been applied or executed. Owner approval is required before
applying it and validating the unchanged drift/mutation tests on the updated head.
Delivery stays draft until this custody gate and applicable CI/review are resolved.
No merge or deployment is authorized.
