# Immediate flashcard review transitions

Date: 2026-10-03 (Europe/Rome). Owner-requested flashcard review latency repair.

Checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/flashcard-review-latency/cardine`.
Branch: `codex/flashcard-review-latency`. Independent base: `origin/main` at
`1f49efd82022238344a3a6b69620204bb020ff84`. No dependency on unmerged tutor,
source-layout, or lesson-selector work.

## Diagnosis and implementation

The original rating dispatch awaited the canonical POST, then a second due
GET before showing the next card, and awaited an advisory bootstrap refresh.
The POST already returns the canonical due payload at its receipt watermark;
the extra GET was unnecessary. A slow server therefore blocked presentation.

Ratings now hide the exact current revision locally and synchronously show the
next row from the already loaded canonical due queue. Answer reveal and rating
of that next card work while persistence is pending. The pending count stays
visible; exhausting the local queue shows saving in progress, not completion.

One browser-local writer serializes pending ratings in click order. Each intent
keeps its revision, rating and request ID, and each POST uses the latest known
course watermark (including the preceding receipt). Detached/stale controls
cannot enqueue the same revision twice. Successful receipts reconcile the due
queue, including legitimately due-again cards. Older reads cannot resurrect an
old queue. Sidebar counts refresh after the writer drains, without blocking
review; the existing undefined `route` reference in that refresh was removed.

Failures pause the writer and restore the first unresolved card. The explicit
retry refreshes canonical state and sends the same request ID and rating, even
when a response was lost after a commit. Only a definitively rejected command
can be explicitly discarded; errors carrying `command_committed` cannot be
cancelled as unsaved. The server now preserves that flag and request ID when
response construction fails after `RecallService.review` returns. Malformed
success receipts remain unresolved instead of dropping the intent.

Changing course/session/account is blocked while review intents remain.
Reselecting the pending review scope is allowed to recover from an external
context change. Navigation within that scope is allowed and late receipts do not replace the
active page. Closing/reloading the page raises the browser's pending-work
warning. **Unsent intents are memory-only**: ignoring that warning or a browser
crash can lose those intents. Confirmed history remains durable on the server;
this change does not introduce offline storage or background delivery.

## Measurements and their limits

Chromium headless on this local machine, three synthetic accepted/enrolled
flashcards, real Cardine HTTP transport, filesystem event store and recall
service, deterministic fixture scheduler; no providers or learner data.
The server delays each review POST by 1,000 ms and each post-review due/bootstrap
read by 500 ms. The metric starts inside the actual click handler invocation,
checks the next card's reveal and enabled rating controls, and crosses two
animation-frame boundaries to include a rendering opportunity. This is a
browser rendering proxy, not display-hardware instrumentation.

| Assets | Viewport | Samples | Median click to next card usable | Maximum |
| --- | --- | ---: | ---: | ---: |
| Base `1f49efd` | 1440×900 | 5 | 1593.2 ms | 1608.7 ms |
| Repair | 1440×900 | 10 | 6.1 ms | 10.3 ms |
| Repair | 390×844 | 10 | 13.1 ms | 29.4 ms |

The repaired desktop POST still took about 1,031–1,044 ms including the injected
delay; the real canonical application work took 28–38 ms. Presentation moved
ahead of that work; persistence itself was not benchmark-optimized.
These numbers do **not** establish production latency, real FSRS latency,
large-corpus throughput, mobile hardware performance, or sustained queue size.

Reproduce from this checkout, with Playwright available on Node's module path:

```bash
PYTHONPATH=src:. .venv/bin/python tests/e2e/recall_review_measure.py --samples 10
PYTHONPATH=src:. .venv/bin/python tests/e2e/recall_review_measure.py --samples 10 --mobile
PYTHONPATH=src:. .venv/bin/python tests/e2e/recall_review_measure.py --samples 5 --baseline-ref 1f49efd
```

The optional measurement runner creates and removes its own synthetic
repository; it never reads a user study repository or calls a model. The
ordinary regression suite requires Node, uses the existing Chrome/CDP helper
for real-browser evidence, and has no added Playwright dependency.

## Verification and delivery

The offline JS regression drives the real dispatch, rendering and writer with
held responses: immediate advance, all four ratings, serial watermarks, double
clicks, uncertain and stale conflicts, exact retries, malformed receipts,
explicit discard, navigation and scope guards. The real-browser test holds a
POST, rates two cards, injects a response loss after the first real commit,
retries, and verifies exactly two ordered canonical reviews and their schedules.
Post-commit capture errors are tested for 400/409/503 with the commit marker and
idempotent recovery. Existing recall atomicity/replay/real-FSRS tests also run.

Full offline suite: **2,829 passed, 50 optional skips**. Ruff, strict mypy,
build and wheel/sdist verification passed. Initial unrelated notes-journey
timeouts did not recur on the full reruns; no source or lesson-selector
product/test files were changed.

Delivery is one scoped PR against main. Only automatic Codex GitHub review,
maximum two rounds. CI/review must be assessed for the submitted head; no merge
or deployment is authorized by this task.
