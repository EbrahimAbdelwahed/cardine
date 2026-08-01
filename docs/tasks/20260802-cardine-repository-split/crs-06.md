# Task Bead: crs-06 Remove Cardine-specific Harness residues and retain generic shell

Status: Open
Priority: P0
Type: task
Depends On: cardine-ui-fix-crs-05-9jk
Run ID: `20260802-cardine-repository-split`
Spec: `docs/specs/separate-cardine-from-study-agent-harness.md`

## Outcome

Study Agent Harness contains no reachable Cardine-specific residue while its generic shell UI, reference browser, TUT-08, public CLI, and tests remain intact.

## Slice Strategy

contract

Fresh Context Fit: yes

## Spec Coverage

- Harness Cardine residual scan is empty.
- Harness generic shell and full suite remain green.

## Grilling Evidence

- docs/flywheel-runs/20260802-cardine-repository-split/grill-with-docs.md
- Decision state: approved
- ADR-0020 and CONTEXT.md forbid removing generic shell UI

## Worker Profile

none needed

Rationale:

No reusable specialization selected yet.

## Context

The public main is already Cardine-name-free, but local branches, worktrees, memory pointers, the prototype archive, and indirect mentions remain.

## What To Do

- Remove or rewrite Cardine-specific memory and workspace artifacts.
- Remove linked Cardine worktrees and local branches only after preservation proof.
- Move then remove the source ZIP from the old workspace.
- Scan every maintained ref, filename, config, import, and remote.
- Run focused generic-shell and full Harness gates.

## Likely Files / Packages

- study-agent-harness local refs/worktrees: Cardine-only cleanup
- dev/index.md and relevant dev memory: durable boundary update
- study-agent-ui/: source archive removal after migration

## Acceptance Criteria

- [ ] No reachable Cardine branch/worktree/file/archive/name remains in Harness surfaces.
- [ ] Generic shell UI/reference browser/TUT-08 files and entry points remain.
- [ ] Focused shell tests and complete Harness gates pass.
- [ ] Harness origin and public history are unchanged.

## Verification

- `rg -n -i --hidden --glob '!**/.git/**' 'cardine|referto|study-agent-ui|cardine-ui|cardine_session|CARDINE_' study-agent-harness`: expected to return no Cardine-specific maintained-surface match
- `git branch --all`: expected to pass or produce documented output
- `git worktree list`: expected to pass or produce documented output
- `python -m pytest`: expected to pass or produce documented output
- `python -m ruff check .`: expected to pass or produce documented output
- `python -m mypy`: expected to pass or produce documented output

## Out Of Scope

- Removing generic shell UI/TUT-08 or pruning unrelated unreachable objects.

## Notes / Handoff

- Worker must report files changed, behavior implemented, verification results, unresolved questions, and follow-up beads.
