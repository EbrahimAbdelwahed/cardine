# Task Bead: crs-01 Preserve and prove every Cardine source

Status: Open
Priority: P0
Type: task
Depends On: none
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

Every Cardine ref, dirty delta, design source, and current canonical tree is recoverable and checksum-verifiable before any destructive operation.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- All Cardine refs, dirty deltas, and the prototype ZIP are covered by a verified bundle/manifest/checksum before deletion.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- ADR-0020; no glossary change required beyond CONTEXT.md

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

Linked worktrees and reflog-only commits make deletion unsafe until all material has an independently verifiable recovery path.

## What To Do

- Create a bundle for all Cardine refs and verify it.
- Hash the bundle, ZIP, canonical tree, and preserved dirty-worktree artifacts.
- Compare dirty worktrees with 707d852 and archive only missing deltas.
- Run a credential and sensitive-path scan over material selected for migration.

## Likely Files / Packages

- /private/tmp/cardine-migration-preservation/: recoverable migration artifacts
- docs/flywheel-runs/20260802-cardine-repository-split/: coordination evidence

## Acceptance Criteria

- [ ] git bundle verify passes.
- [ ] Manifest names every Cardine branch/worktree and exact SHA.
- [ ] Every dirty or untracked delta is proved duplicated or separately archived.
- [ ] No secret value is selected for migration.

## Verification

- `git bundle verify /private/tmp/cardine-migration-preservation/cardine-refs.bundle`: expected to pass or produce documented output
- `cd /private/tmp/cardine-migration-preservation && shasum -a 256 -c SHA256SUMS`: expected to pass or produce documented output
- `git fsck --full --no-reflogs`: expected to pass or produce documented output

## Out Of Scope

- Deleting refs, worktrees, or reflogs.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
