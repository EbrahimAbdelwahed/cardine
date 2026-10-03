# Local latency and native provider drafts

Owner-requested implementation, 2026-10-02. Checkout:
`/Users/ebrahimabdelwahed/.codex/worktrees/bd6b/cardine`, branch
`codex/local-latency-streaming`, based on fetched `origin/main` at `9465dae`.
No merge or live rollout is part of this change.

## Evidence and scope

The live loopback preview remained on its separate `main-preview` checkout at
`18f941a`. Its v1 configuration had Jev routing disabled. At the owner's request,
the configuration was backed up and migrated to v2 with tutor routing ON;
existing consent and credential references were preserved. No model request
was sent as part of configuration activation.

The observed routed explanation took 50.801 seconds: 4.542 before the decision,
0.520 for routing, 21.920 before grounded-answer generation, 8.769 in generation,
4.735 remaining capability work and 10.306 afterwards. Activity reads were
sub-millisecond, but the browser only revealed answer words after the completed
receipt. Initial session/source/recall HTTP reads each took roughly 6.2–6.6 seconds.

The cold bootstrap profile had 42.7 million Python calls, 12 projection loads,
four catalog decodes and repeated PageIndex/source work. An offline explanation
on an isolated copy of the same course made 294 million calls under profiling,
decoded the catalog 42 times and resolved a lesson repeatedly. The largest
canonical blob is 194.37 MiB. Source/projection reconstruction, deep freezing,
full-source document construction and repeated blob hashes dominated local work.

## Implementation

SQLite caches immutable decoded values only after rereading guarded canonical
rows and comparing their exact bytes and sequence. Source catalogs reuse
decoded manifests after rereading canonical events and verifying actual blob
bytes. Generated-source admission caches also bind their historical event prefix.
Validated retrieval documents and lesson candidates are reused within a lease.
Owned deeply frozen JSON can be reused; untrusted mappings and proxies are copied.

Filesystem blob reads still use descriptor-anchored regular-file checks and
reread the entire file. A bounded cache reuses a digest only after exact byte
equality with previously hashed content for the full BlobRef. Corruption,
symlinks, missing files and subsequent recovery remain observable.

UI reads defer startup index backfill and build only the payload needed by the
requested route. Explicit indexing/reconciliation remains responsible for work.
Scoped explanations skip redundant query recovery and lesson resolution.

Grounded Luna explanations consume real provider SSE when a live draft observer
is present. Only partial segment text reaches a memory-only draft endpoint; the
browser updates it every 100 ms as plain text marked unverified. Native stream
completion, protocol errors, timeout and cancellation close the stream. Final
parsing, citation validation and canonical publication remain unchanged. The
final receipt replaces the draft without an artificial word reveal. The original
decision/tool/flashcard generation paths are preserved.

The owner's follow-up requested dead-code removal. The old word-by-word reveal,
unused staged reveal, related CSS/animations, timer/DOM machinery and browser
reveal tracking/callbacks are deleted. DOM tests now cover immediate final text,
real provider drafts, actions and navigation. Duplicate custody row validation
loops share one strict exact-scope helper. The first full test run found a text
representation regression in frozen objects; the previous string/repr contract
is restored rather than changing prompts or weakening its existing test.

See [ADR-0024](../../docs/decisions/ADR-0024--local-read-budgets-and-live-drafts.md)
and the exact-byte `tests/parity/local-latency-overlay.json` custody binding.

## Measured comparison

All turn comparisons used an isolated copy of the course and the same offline
fixture model, features OFF, one grounded-generation call and no provider spend.
These figures measure local work, not Jev/provider response latency or quality.

| Measurement | Original | Final |
| --- | ---: | ---: |
| Offline turn, without profiler | 49.776 s | 6.202 s |
| Before offline decision | 4.456 s | 0.499 s |
| Final generation start after submission | — | 4.538 s |
| Bootstrap read | ~6.6 s live | 1.692 s copied course |
| Session read | ~6.4 s live | 0.893 s copied course |
| Artifacts read | ~6.4 s live | 0.280 s copied course |
| Materials read | ~6.4 s live | 0.860 s copied course |
| Recall read | ~6.2 s live | 0.224 s copied course |

The page comparison crosses HTTP/application boundaries and uses copied data;
it is directional evidence, not a controlled browser paint benchmark. Warm
profiling before the final blob optimization reduced the offline turn from
90.707 to 9.942 profiled seconds. Profiler overhead is excluded from the primary
turn comparison. There is still material local work before the first token:
roughly four seconds after the offline decision on this large course, and
1.664 seconds after the fixture generation starts. Further improvement and a
real provider/browser timing run remain possible; instantaneous tokens are not
claimed by the offline measurements.

## Regression coverage and delivery

Deterministic Python-call budgets cover a 400-section source: below two million
for chat reads and four million for explanations. The chat baseline exceeded
7.8 million calls. A repeated-read hash-work budget covers unchanged canonical
blobs. Warm caches must detect external appends, same-sequence projection edits,
same-length blob corruption and changed historical HUMAN admission.

Offline SSE tests require text before provider completion, no canonical history
write for a partial draft, rejection of unknown evidence, UTF-8 decoding, native
connection cleanup, typed sanitized errors and cancellation propagation. Browser
DOM tests cover safe draft text and stale navigation polling. A dedicated CI job
installs the OpenAI extra and runs these tests on Python 3.12 and 3.13 without
provider calls. Provider-free imports remain a separate CI gate.

Delivery: [PR #15](https://github.com/EbrahimAbdelwahed/cardine/pull/15),
implementation commits `9eb37f3` and `c949256`. The cleanup commit removes 226
lines and adds 76 across code, tests, custody and this log. Full pytest on the
cleaned code: 2,867 passed, four optional smoke tests skipped in 114.13 seconds.
Ruff, mypy (667 files), wheel/sdist build, package verification, ownership audit
and JavaScript syntax checks passed. The documentation-only handoff update
follows these implementation checks; current CI and automatic review evidence
are tracked on the PR. Only the initial automatic GitHub review is requested
at ready transition; no third review round or merge is authorized.

The running server needs a deliberate checkout update and
restart to use the patch; its process-local credentials must be preserved or
supplied again. No live source mutation, paid generation, merge or rollout was
performed for these measurements.

The initial automatic review of `fa0b2ec` reported two P2 issues: streamed
provider rejections lost their specific error code, and polling stopped after
144 seconds despite an active turn. The fixes read at most 64 KiB of error
payload solely for sanitized classification, and poll until settlement or
navigation/transport cancellation. Offline tests cover schema/endpoint/model
errors, oversized-body early stop and drafts arriving beyond the old ceiling.
Main advanced to `1f49efd` (selected lesson notes, PR #14); its changes are
integrated without discarding selection/retry identity. The custody helper
validates both old/selected scopes and applies the final latency bindings last.
The next automatic review is round two and the final permitted round.
