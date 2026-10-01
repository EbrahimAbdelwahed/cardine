# PR #9 current review repairs

Checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/pr9-source-study-notes-review-fixes/cardine`.
Working base: `c7888658a09d60b6ba9d321e2b5d4a9fc10d0406`, detached from
`codex/source-study-notes` because that branch is checked out by another owner.
The parent task owns cherry-picking this scoped commit into the existing PR #9
branch after coordinating the concurrent PR work. Do not push or open a PR here.

Publication now marks a proposed material job stale when its pinned root source
has been retired or superseded before an accepted output can publish. The
existing HUMAN decision stays accepted, the output remains unpublished, and no
publication retry is recorded. Regeneration needs a new run against the current
source revision.

Audio extraction now checks canonical manifest size against the existing
2 MiB provenance bound before admission. An oversized timestamp manifest ends
in `failed_terminal`; its content-addressed bytes and reference remain alongside
the completed transcript chunk checkpoints, so reopening cannot repeat the
same work indefinitely.

Focused validation: `tests/integration/test_material_product.py` passes (16
tests), Ruff passes on the changed Python files, and `mypy` passes for
`src/cardine/materials/product.py` using the existing primary-checkout venv.
Ownership audit passes (322 rows), and the feature custody overlay binds the
updated product digest. GitHub could not be reached from this environment, so
PR/CI status is unchanged and must be checked by the parent before publication.
