# Handoff: Wave A temporary worktree loss

Date: 2026-08-12 15:15
Area: Cardine / Wave A / recovery

## Current State

The isolated Cardine implementation checkout `/private/tmp/cardine-ca02-clean`
was removed externally after commit `2d9486c` was created and verified. The
checkout's Git object database was no longer present. At the time of this
handoff, neither the durable workspace repository nor the surviving Harness
worktree contained the Cardine Wave A objects.

## Completed Before Loss

- CA-02 final namespace transition and Cardine integration seam.
- Provider consent and source retirement.
- Qualified, hash-bound PageIndex structural subset and restart-safe projection.
- Lesson search, explicit selection pins, grounded chat, and canonical citations.
- Reviewable flashcard proposals and accepted-only recall.
- Selected-lesson flashcard generation with a restart journey.
- Final focused checkpoint at `2d9486c`: 27 passed and 5 socket-sandbox skips;
  Ruff, mypy, JavaScript syntax, and diff checks passed.
- The final partial-pin/provider finding was fixed and proved provider-zero.

## Recovery Evidence

- The temporary checkout and its Git object database could not be recovered.
- The remote did not contain a Wave A branch.
- The workspace main checkout was unrelated and materially dirty, so it was not
  reset or repurposed.

## Recovery Requirement

- Reconstruct from the durable Cardine ancestor using committed plans, logs,
  tests, and handoffs as contracts.
- Store the reconstruction in a durable path.
- Push or bundle reconstructed checkpoints before using temporary worktrees.
- Do not claim lost behavior exists until it is reconstructed and reverified.

## Resolution

Wave A was reconstructed in `cardine-wave-a-recovery`, closed at `502a688`, and
the usability fixes were published at `095fb75`. The current operational state
is recorded in the [current Wave A handoff](2026-08-13-1410--cardine--wave-a-usable-recovery--handoff.md).
