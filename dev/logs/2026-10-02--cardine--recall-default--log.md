# Scheduled recall by default

Owner request: configure Ripasso now and by default. Isolated branch
`codex/recall-default` depends on PR #12 at c6cc451. The original dirty
Desktop/Dev/cardine checkout and the PR #12 checkout were preserved.

`cardine-shell-web` now supplies a lazy PyFsrsSchedulingPolicy factory.
FSRS 6.3.1 is a standard product dependency; the recall extra is retained
for compatibility. Core recall remains provider-neutral and explicitly
configured; factory failures retain the existing unavailable behavior.
The locked package artifact and version are unchanged.

Validation: 16 recall/real-FSRS/architecture tests pass. Full offline suite:
2544 passed, 4 skipped, 1 failed in the inherited clean-archive ownership
audit from PR #12. Ruff passes; strict mypy passes on 622 source files;
wheel and sdist build and verify_cardine_wheel pass. uv lock --check --offline
passes. The new startup test accepts/enrolls a card, records a real FSRS
rating and recovers identical due state after starting the CLI again.

Local Wave A server runs from this worktree at 127.0.0.1:8765 with the
existing repository, course-wave-a/session-live. Health is OK and recall
returns ready/available with six due cards. No live review, enrollment or
model call was made by verification. Credentials are process-local and
need re-entry after the restart.

The owner subsequently asked about source note generation. PR #9 merged
to main on 2026-10-01, but PR #12 does not include that merge. The preview
currently follows PR #12 plus this recall fix; combining current main
with PR #12 is a separate requested clarification, not silently performed.
GitHub CI/automatic review and inherited ownership gate remain pending;
no merge or deployment performed.

## Merge preparation after PR #12 delivery

Owner requested merging PR #13. Its base is now main, including merged PRs
#10 and #12. The sole merge conflict was the development-index update date;
both feature records are retained. No production conflict required a behavior
choice. The standard-server FSRS factory remains the PR's three-line change.
The two effective browser.py custody commitments now bind its exact bytes,
and the existing mutation test also protects browser.py from further drift.

Verification on integrated runtime commit 7664b5f: 2852 offline tests passed,
4 expected optional/opt-in skips in 142.38s. The 13 recall journeys pass,
including default CLI startup, real FSRS and restart. Ruff, strict mypy
(662 files), uv lock --check --offline, wheel/sdist build and package verification
pass. Final-head GitHub CI and the first automatic Codex review are pending.
The owner instructed merging after at least one review, without further cycles;
CI must still pass. No live study-store mutations, provider calls or deployment.
