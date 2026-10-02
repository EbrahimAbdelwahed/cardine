# Generated-source lifetime staleness

Date: 2026-10-01
Area: generated-source admission and canonical source lifetime

## Finding

The generated-source projection checked only the collapsed latest source
lifetime status. A root retired after a material proposal and restored before
materialization therefore appeared eligible, even though its unpublished run
had become stale.

## Change

Admission now compares the latest canonical restore receipt with the proposal
batch's recorded time. A root restored after that proposal is rejected before
canonical source append. A fresh proposal recorded after restoration remains
eligible. The projection test harness now reduces canonical source-lifetime
events, and behavior tests cover both stale and fresh runs while checking that
replaying the proposal-prefix projection remains unchanged.

## Verification

The generated-source materializer integration tests pass (11 tests), as do
Ruff, focused strict mypy, and `git diff --check`. The ownership audit currently
reports a digest mismatch for the concurrently edited flashcard router; that
row is owned by the routing fix and will be updated separately. No network,
provider, push, review, merge, or CI operation is part of this fix.
