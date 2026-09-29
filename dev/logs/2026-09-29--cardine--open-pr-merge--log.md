# Owner-requested open PR integration

The owner requested merging Cardine open PRs on 2026-09-29. This work uses the
isolated managed cardine-merge-prs checkout; Desktop/Dev/cardine dirty files
and the unpublished 409e418 recovery continuation are preserved.

PR #5 starts at 5aeadff. Three actionable automatic Codex findings are repaired:
numbered lesson pins stop at enclosing unrelated headings while preserving
same-lesson continuation; stage preflight preserves consent-required failures;
immediate turn receipts include server-resolved canonical citation identities.
Behavior tests cover each trigger, including zero further provider calls after
consent revocation.

The merge instruction authorizes integration and necessary review fixes. The
temporary exact-byte recovery overlay updates only repository.py and
ui_application.py for these fixes; the 46-path universe, original approval
checkpoint, historical ownership ledgers and fail-closed audit are retained.
Installed Harness parity (CA-08) and copied-core removal (CA-10) remain pending.

Merge still requires current submitted CI and automatic Codex review evidence.

PR #5 verification: 2493 passed, 4 optional tests skipped; focused contracts
44 passed; Ruff, mypy (621 files), standalone ownership audit pass.
Isolated wheel/sdist build and artifact verification also pass.

PR #7 is updated on the recovery base, resolving the dev index conflict by
keeping both entries. The automatic Codex finding on off-route streaming is
fixed: a reply that completes after navigation is marked revealed, so returning
to the session shows its verified answer immediately. The included Beautiful UI
MIT notice is bound to its exact bytes in the custody audit as a separate
third-party artifact; the historical CA-01/CA-02 rows remain unchanged.

A later Codex review on #5 found historical generated-source reads used the
latest projection. Generated admissions now validate against canonical event
prefix replay at their original sequence; the latest projection is still
checked for consistency. Post-admission root revision changes cannot make
valid historical generated sources unreadable.

The same review found that presentation text could forge clickable canonical
source chips by appending a `Fonti verificate` block. Explanation completion now
records source, revision, and locator refs from the verified capability output;
the fingerprinted presentation receipt and canonical event carry them through replay.
The UI resolves chips only from those refs and matching catalog records. A
model-authored source heading without refs creates no canonical citation.

The review repairs update exact digests for the touched post-baseline and
46-path recovery bytes. Two copied session paths have explicit AST variance
entries bound by those digests; historical CA-01/CA-02 files stay frozen. On
this checkout the complete suite passed (2489, 13 optional skips), Ruff and
wheel/sdist build passed. The added historical replay test and final mypy run
are checked before publication.
