# Log: Cardine development-memory consolidation

Date: 2026-08-13 14:20 CEST
Area: Cardine / development memory

## Summary

Restored a self-contained Cardine memory tree after Wave A recovery had left the
current index and recovery handoff in the workspace-level archive. Cardine now
has one authoritative `dev/index.md`, the original Wave A contract, the historical
loss/recovery handoff, and a current operational handoff.

The preserved AnyDoc plan/log were added to the reachable memory tree and marked
as verified local work whose implementation is not yet part of the published
fix commit.

## Verification

- Every relative Markdown link under `dev/` resolves.
- `git diff --check` passes.
- The current handoff distinguishes published Wave A work, transient live-server
  state, optional fix 5, and unrelated local AnyDoc changes.

## Notes

- The workspace-level `../dev/` directory remains historical archive; it is no
  longer the current Cardine entrypoint.
- Future material Cardine changes must update the repository-local log/handoff
  and keep `dev/index.md` navigable.
