# Publish the flashcard generation repair to PR #5

On 2026-09-29 the owner asked to publish the diagnosed generation repair in
existing PR #5. Preparation starts from its submitted head `5aeadff` in an
isolated, Codex-managed worktree. The original dirty checkout, combined local
commit `409e418`, other worktrees and running preview are preserved.

The four repair/test files are copied byte-for-byte from `409e418`. The schema
projection supplies `items` for empty-only arrays while retaining `maxItems=0`,
existing element schemas and the untouched local validation contract. Failed
lesson generation now reports a failed capability rather than insufficient
evidence. A hash of the canonical learner interaction distinguishes a fresh
turn from replay of the same request, keeping restart replay idempotent while
allowing a new turn to generate after failure. Generated cards remain proposals.

This publication excludes the recall-host changes bundled in the local commit.
The owner's publication instruction covers the repair and its necessary two-path
custody update. Only the hashes for `src/cardine/adapters/model/openai_luna.py`
and `src/cardine/application/flashcard_proposals.py` change in the recovery
overlay. Its original source commit, other 44 rows, historical ownership ledgers
and audit logic remain unchanged. Installed-package parity and copied-core
removal gates remain open.

Tests use offline fixtures; no provider calls, credentials, study-store
mutations or artifact decisions are involved. The current preview is a separate
checkout at `425aeac` and does not receive this repair through publication alone.
No merge or preview restart is authorized by this publication request.

Validation before commit: full offline suite 2491 passed, 4 skipped (134.61s);
Ruff passed; mypy passed on 621 source files; wheel/sdist build and both artifact
checks passed; standalone ownership audit passed (322 rows); diff check passed.
The full suite's clean-archive cases inspected the unchanged submitted base.
The four clean-archive cases must also pass on the actual new commit before push.
Automatic GitHub review and CI must be assessed against the newly submitted head;
the existing automatic review at `08ea435` is historical evidence only.
