# Handoff: Cardine repository split at GitHub authentication gate

Date: 2026-08-02 01:41 CEST
Area: Cardine / repository split

## Current State

The approved split is executing through Flywheel run
`20260802-cardine-repository-split`. CRS-01 preservation is complete. CRS-02
is ready but awaits successful GitHub CLI authentication before the private
remote can be created.

## Completed

- Confirmed canonical linked worktree `/private/tmp/cardine-ui-fix`, branch
  `codex/cardine-ui-enterprise`.
- Recorded `CONTEXT.md`, ADR-0020, approved spec, seven task beads, worker
  briefs, and CRS-01 worker report in commit `5ebe751`.
- Preserved 45 recovery artifacts under
  `/private/tmp/cardine-migration-preservation/`.
- Verified bundle with 74 refs plus 31 unreachable commits, canonical head
  `707d852`, OSS base `e18f670`, 43 checksums, all dirty/untracked worktrees,
  prototype ZIP, design source, credential scan, and Git fsck evidence.
- Confirmed through the GitHub connector that
  `EbrahimAbdelwahed/cardine` does not currently resolve.
- Ran focused baseline tests: sandboxed run had socket-only failures; permitted
  local-socket rerun produced 54 passed and one pre-existing stale assertion
  because `/health` now also returns `runtime_id`.

## Remaining

- Complete `gh auth login` and verify `gh auth status`.
- Create private `EbrahimAbdelwahed/cardine`.
- Push exact `707d852` as the initial `main`, then the split checkpoint.
- Verify a clean clone and convert the canonical path into an independent
  clone without changing Harness `origin`.
- Execute CRS-03 through CRS-07.

## Important Context

- Generic shell UI, reference browser, and TUT-08 must remain in Study Agent
  Harness.
- Do not stage `.cardine-ui-preview/` or `.beads/`; they are local generated
  state.
- Do not remove any Cardine worktree or branch before the remote and clean
  clone gates pass.
- The current linked worktree shares the Harness Git directory and remote;
  never replace its `origin` in place.
- CRS-03 audit found stale product identity, ambiguous root licensing, and a
  pre-existing Docker command mismatch (`--public-demo` is no longer a valid
  browser CLI option).

## Verification

- `git bundle verify`: passed; complete history.
- `shasum -a 256 -c SHA256SUMS`: 43 entries passed.
- `git fsck --full --no-reflogs`: exit 0; dangling objects documented.
- Flywheel dispatch validation: passed with zero issues.
