# PR #3 automatic review fixes

Checkout: isolated shared clone at `/private/tmp/cardine-pr3-review` on
`agent/cardine-architecture-reliability-latency`, based on review head
`de370281dcbf99944d421624a218181ee36ad256`.

PR: [#3](https://github.com/EbrahimAbdelwahed/cardine/pull/3). This handoff
covers two automatic review findings. The current learner turn's explicit
lesson reference now controls flashcard scope even when the model reduces its
query to a topic; the nearest current reference fails closed when unavailable.
Normal turn receipts now settle live Tool Chips from the returned tutor status,
including failed, terminated, cancelled, and budget-exhausted outcomes. Accepted
assistant messages and learner-input continuations remain settled as successful.

Regression coverage includes the `glicolisi` distilled-query trigger, unresolved
nearest-scope precedence, normal failed-turn receipts, and status-to-activity
mapping. The recovery custody overlay binds the new exact bytes for
`src/cardine/cli/repository.py` and `src/cardine/demo/ui_application.py`; the
historical ownership ledgers remain unchanged.

Focused offline tests: 24 passed, 1 socket-binding test deselected because this
sandbox denies loopback binds. Ruff passed on all five changed Python files; mypy
passed on 630 source files; `scripts/audit_harness_ownership.py --check` passed
(322 rows); and `git diff --check` passed.

This is an unpublished worker commit for PR #3. The coordinating task owns
publication after integrating the other approved fixes. GitHub inspection was
unavailable from this worker because DNS could not resolve `github.com`; PR
state and CI remain for the coordinator to verify.
