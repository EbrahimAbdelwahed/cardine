# Page-load latency and regression gates

Owner request: add page-loading regression tests and reduce page loads to at most
three seconds where possible, preserving product behavior. Work started
2026-10-04; final verification 2026-10-05 (Europe/Rome).

Checkout: `/Users/ebrahimabdelwahed/.codex/worktrees/01ac/cardine`.
Branch: `codex/page-load-latency`, independently based on fetched main `6e9c904`.
No unmerged dependency. No merge or live restart is authorized by this task.

## Evidence

Read-only live HTTP measurements: bootstrap 5.641 s, Fonti 2.143 s, Chat 2.076 s,
Ripasso 0.325 s. A SQLite-backup copy of the course includes committed WAL data;
original blobs and configuration are copied into a private temporary directory.
Only the copy is opened by the measurement app, with background indexing and
material recovery disabled and no provider credentials. Raw study data is never
committed. The production server and its canonical study store are unchanged.

Before fixes on that copy: bootstrap 6.268 s, Chat 3.141 s, Fonti 2.278 s.
Bootstrap profiling found two complete PageIndex status reads (4.299 profiled
seconds), repeated original hashing, and duplicate selected-context checks.
After removing repeated PageIndex/hash work: bootstrap 2.427 s, Chat 1.250 s,
Fonti 1.368 s, all remaining read APIs below 0.32 s. These are application
measurements, not browser paint measurements. The first complete browser startup
on the copied course was still 3.1–3.2 s and was correctly rejected by the
three-second assertion; final measurements are recorded below after verification.

The synthetic fixture uses 194 MiB original bytes, 559 exact page spans, over
3,900 canonical chunks, ready PageIndex, a pending proposal and one due real-FSRS
card. All nine product pages are tested at 1440 and 390 px, twice each. Fonti
waits for material-generation loading as well as the source library. Provider
requests and canonical event changes fail fixture teardown.

The three new deterministic work guards were run against an archive of main:
all failed, observing two structure summaries, three original hashes and two
PageIndex source hashes. They pass against this branch. The browser/API tests
use the fixed three-second threshold, without retries to hide slow samples.

## Implementation and boundaries

See ADR-0026 for the response-local sharing of verified structure/fingerprints
and already-verified digests. Source bytes and index payloads are still validated
on every read. Historical identity checks, HUMAN decisions, citation integrity,
authentication, consent and credential boundaries retain their existing owners.

A new CI job requires Chromium and runs the page budgets explicitly. The existing
full-suite jobs also collect these tests. The three-path custody overlay preserves
all earlier approval manifests and does not claim installed-package migration.

## Final browser measurements on the isolated course copy

After removing the snapshot pre-reads, the same real-course browser assertion
passes at both widths, including initial startup and two visits to every page.
Initial startup: 2.764 s desktop, 2.590 s mobile. Maximum visited-page times:
Oggi 2.538 s, Chat 1.450 s, Fonti 2.913 s including historical generation jobs,
Proposte 0.316 s, Verifiche 0.316 s, Percorso 0.332 s, Ripasso 0.365 s,
Piano 0.282 s and Settings 0.034 s. There are no browser runtime errors.
These samples are evidence for this course and machine, not a universal latency
promise. The original live process has not been updated.

The final synthetic browser gate also passes: initial 1.733 s desktop and
1.654 s mobile; all route samples below 1.61 s, with no provider requests or
canonical event changes. The work guards fail on main and pass on this branch.

## Verification and delivery

Final local verification on implementation commit `5d28773`: 3,007 tests passed,
four optional/network/PDF-containment smoke skips, in 233.22 seconds. Browser,
source-integrity, PageIndex, read-coherence and clean-archive custody cases all ran.
Ruff, strict mypy (682 files), the 322-row ownership audit, offline wheel/sdist
build and exact packaged-source verification pass. The new required GitHub
`page-loads` check is green on the implementation commit, along with ten other
checks; the two full Python jobs were still running at the documentation update.

Published as [PR #25](https://github.com/EbrahimAbdelwahed/cardine/pull/25).
This documentation-only continuation records completed verification; local work
is committed and pushed on the same branch. Final-head CI and the initial
automatic Codex GitHub review are tracked on the PR, with a two-round limit.
No paid provider calls, merge, data migration, deployment or live restart occurred.
The running server still needs an owner-authorized update to receive this patch.
