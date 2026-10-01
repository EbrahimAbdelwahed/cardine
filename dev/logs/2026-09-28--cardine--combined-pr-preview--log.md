# Combined recent-PR test preview

Owner requested a runnable Cardine version containing recent PR changes.
Task checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/cardine-pr-preview/cardine`.
Branch: `codex/cardine-pr-preview`, initially created from fetched `origin/main`
at `1163005`. The original `Desktop/Dev/cardine` checkout remains on
`codex/cardine-testing-CLI`; its dirty and untracked files were preserved.

Integrated submitted heads:
- PR #5 recovery: `5aeadffc38ad06bbf473fbf4a828ae688d05a17f`, including
  merged PR #6 message scroller and the existing main/PR #4 baseline.
- PR #3 reliability: `427c49a0a2c322f83eca51e59eb314f67bda41af`.
- PR #7 compact activity and verified response presentation:
  `961e6269408141bcab35a65414eb41cb6fc8c23b`.

This is a combined integration preview. Any PR for it must name these unmerged
dependencies and use main as its base; its broad product lineage is intentional.
It does not replace or authorize merging the individual PRs.

Merge resolution preserves the recovery audit and its exact custody checks,
stronger retry/trace assertions, supported AnyDoc containment guards, and Luna's
schema override hook. Generic provider projection from PR #3 runs through that
hook. The message scroller and PR #7 controls coexist. Two PR #3 test annotations
were narrowed without changing their behavioral assertions. The unpublished
`409e418` repair and original-checkout local repairs are excluded.

Verification:
- Full offline suite on the complete combined sources: 2,510 passed, 4 skipped,
  1 failed (the clean-archive custody baseline test).
- Final changed schema/fast-path/scroller/thinking tests: 14 passed.
- Ruff, mypy (628 files), JavaScript syntax, and diff checks pass.
- Wheel and sdist build; both artifact verification checks pass.
- Real in-app browser loads the copied course and conversation, with message
  navigation, response actions and follow-ups present. Health/settings return
  HTTP 200; credential_configured is false.

The exact ownership/custody gate remains failing for the combined PR bytes and
new MIT notice, as expected from the unchanged recovery baseline. No ledger,
classification, audit bypass or approval hashes were changed. This preview is
not merge-ready; GitHub CI and automatic Codex review remain to be assessed.

Local runtime: `http://127.0.0.1:8766`, course `course-wave-a`, session
`session-live`. Its study repository is a separate private snapshot at
`/Users/ebrahimabdelwahed/Desktop/Dev/cardine/.cardine-ui-preview/pr-preview/study-repository`.
SQLite backup APIs and integrity checks copied the three nonempty databases;
blobs/config were copied, and the snapshot has its own lock. Original data is
untouched. Preview writes persist only in this snapshot and are not synchronized.

The ignored local launcher at
`/Users/ebrahimabdelwahed/Desktop/Dev/cardine/.cardine-ui-preview/pr-preview/serve.py`
uses this checkout's editable environment and the existing
`PyFsrsSchedulingPolicy` through the public scheduler factory, because submitted
browser.main does not yet compose it. This is launcher composition only; no
scheduler implementation or published browser source was replaced. The same
folder holds server.pid/server.log. To restart, stop only that saved PID and run
the launcher with this worktree's `.venv/bin/python`. The Settings key is
write-only and process-local, so the owner must enter it after each restart.
No model calls, source uploads, artifact decisions, enrollment or recall ratings
were made by this task. Study stores, raw logs, credentials and build outputs
remain outside Git. Preserve this active worktree while the preview uses it.

## Authorized merge integration — 2026-10-01

The owner requested merging the open PRs. Integration now reconciles PR #5
`8a43343`, PR #3 `de37028`, and PR #7 `efb6ee9` in the same preview PR.
The historical preview above remains a record of its initial state. The exact
custody gate is now repaired with scoped source digests and reviewed AST
variance rules; frozen historical classifications remain preserved.

Full offline integration before the final lesson-scope fix: 2,555 passed,
4 optional network tests skipped. The final scope fix has 16 passing focused
scope/completion tests; Ruff, mypy and ownership audit pass. GitHub CI and
automatic Codex review must validate the submitted final commit before merge.
PR #9 source study notes remains a separate outcome and is not part of this
preview. The original checkout and private preview study data are preserved.

## Final review-fix integration — 2026-10-01

The owner authorized finishing the open-PR merges and parallel Luna workers.
All seven current review findings have scoped fixes on their existing PRs.
This preview integrates final recovery #5, reliability #3 and chat #7; #9
remains a separate outcome. The local combined suite passes 2,571 tests with
four optional skips. Ruff, mypy and exact custody pass. The primary checkout
and private study data remain untouched. Publish once per PR after final
verification; require current-commit GitHub CI and automatic Codex review
before merging in dependency order #5, #3, #7, #8, #9.
