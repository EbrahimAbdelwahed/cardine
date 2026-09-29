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
