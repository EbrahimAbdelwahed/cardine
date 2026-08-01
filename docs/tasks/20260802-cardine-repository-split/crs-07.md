# Task Bead: crs-07 Close migration with remote audit, tag, and durable handoff

Status: Open
Priority: P1
Type: task
Depends On: cardine-ui-fix-crs-06-nhr
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

Both repositories have auditable final state, exact verification logs, a Cardine migration tag, and no open migration work.

## Slice Strategy

contract

Fresh Context Fit: yes

## Spec Coverage

- Remote audits, tag, logs, and handoff close the split.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- No ADR/glossary change: closure only

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

The split is not complete until clean-clone and remote evidence make the new ownership discoverable and recoverable.

## What To Do

- Audit Cardine and Harness remote refs/visibility/default branches.
- Create and verify the annotated Cardine migration tag.
- Write final logs and any handoff with exact commands/outcomes.
- Update durable indexes without reintroducing Cardine into Harness active-work pointers.

## Likely Files / Packages

- Cardine dev/logs and dev/index.md: migration record
- Harness dev/logs and dev/index.md: cleanup record

## Acceptance Criteria

- [ ] Cardine tag resolves to the approved verified commit.
- [ ] Both clean clones reproduce green verification.
- [ ] No unresolved task or destructive follow-up remains.
- [ ] Durable memory explains the independent ownership boundary.

## Verification

- `git ls-remote --heads --tags https://github.com/EbrahimAbdelwahed/cardine.git && git ls-remote --heads --tags https://github.com/EbrahimAbdelwahed/study-agent-harness.git`: expected to pass or produce documented output
- `gh repo view EbrahimAbdelwahed/cardine && gh repo view EbrahimAbdelwahed/study-agent-harness`: expected to pass or produce documented output
- `flywheel runner final validation`: expected to pass or produce documented output

## Out Of Scope

- Future upstream synchronization or feature work.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
