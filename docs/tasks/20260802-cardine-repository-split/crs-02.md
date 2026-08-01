# Task Bead: crs-02 Create and verify the autonomous private Cardine repository

Status: Open
Priority: P0
Type: task
Depends On: cardine-ui-fix-crs-01-5ie
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

The private Cardine remote exists, main preserves 707d852, and /private/tmp/cardine-ui-fix is an independent clone with its own Git directory and origin.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- Private repository exists with preserved history.
- Canonical Cardine path is an independent clone.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- ADR-0020; no further ADR/glossary change

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

The current linked worktree shares Harness remotes, so remote publication and in-place path conversion must be gated and orchestrator-owned.

## What To Do

- Create EbrahimAbdelwahed/cardine as private without overwriting an existing repository.
- Push only 707d852 ancestry as main via a temporary remote.
- Verify SHA/tree from a clean clone.
- Recreate /private/tmp/cardine-ui-fix as the independent clone and remove the temporary Harness remote.

## Likely Files / Packages

- /private/tmp/cardine-ui-fix/.git: independent repository metadata
- study-agent-harness/.git/config: temporary remote only

## Acceptance Criteria

- [ ] Remote visibility is private.
- [ ] Remote main resolves to 707d852 before normalization commits.
- [ ] Canonical path has a real .git directory and Cardine origin.
- [ ] Harness origin is unchanged and temporary Cardine remote is absent.

## Verification

- `gh repo view EbrahimAbdelwahed/cardine --json nameWithOwner,visibility`: expected to pass or produce documented output
- `git rev-parse HEAD`: expected to pass or produce documented output
- `git remote -v`: expected to pass or produce documented output
- `git worktree list`: expected to pass or produce documented output

## Out Of Scope

- Rebranding source files or deleting other Cardine worktrees.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
