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

## 2026-10-02 owner-approved merge preparation

The owner explicitly approved the two custody proposals and the PR #12 conflict
choices. The journal overlay binds exactly 25 paths and two removals. Its tests
mutate every bound source, restore each removed file, omit a binding and attempt
to add a path outside the scope; all are rejected. Historical manifests remain.
Main is integrated through merged PR #10 at fe0f967. Both student-state and
material-generation APIs remain; student_state.record/search replace retired
memory tools; failed activity settlement remains truthful. Two tests newly
inherited from main were aligned with the approved journal routes/tool names.

Full offline integrated suite on 5397384: 2849 passed, 4 opt-in/optional skips,
2 failures from the old test expectations above. After those test-only repairs,
all 12 relevant product/fast-path tests pass. Ruff and strict mypy (662 files),
wheel/sdist build and artifact verification pass. Final-head GitHub CI and the
first automatic Codex review must complete before the requested merge.

The owner explicitly instructed merging everything that has had one review,
without further review/fix cycles. PR #10 was merged with all ten CI checks
successful and two Codex rounds completed. Outstanding #10 findings retained:
reconciliation after the first four sources; resolved-model pin in routing;
lifecycle manifests for semantic config. That instruction applies to PR #12
once its first review completes; it does not waive failed CI.
