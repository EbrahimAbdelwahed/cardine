# PR 22 merge preparation and local server update

The owner requested merging PR 22 and updating the existing local server after
the retrieval reliability audit. PR 22 contains the offline benchmark and audit;
it does not enable keyword climbing in the production tutor.

Integrate main `34a3e31`, which already includes PRs 18–21. The only manual
conflict is `dev/index.md`; retain both the retrieval audit entry and all four
recent delivery entries. No runtime source or custody overlay requires manual
resolution. The previously merged PRs' outstanding automatic findings remain
documented in their existing handoffs and are not findings introduced by PR 22.

At initial inspection PR 22 had no CI or review evidence; its conflict prevented CI.
Require CI and automatic GitHub Codex review of the submitted integration head
before merging. Automatic round one subsequently reported a P2: the benchmark
trusted same-sequence persisted projections for policy and retirement state.
The fix verifies every course against canonical event replay before constructing
any projection-backed view and fails without repair. Four regressions cover both
fields in the selected course and another course. The integrated full suite
passed 2,996 tests with four optional smoke skips before this fix. Request only
the final follow-up round after publishing the fix and its validation; the
repository limit is two rounds total. Do not merge based on an older green head.

The running local server uses `merge-open-prs-8901/cardine` on clean main
`34a3e31`, port 8765, store `cardine-wave-a-live`, course `course-wave-a` and
session `session-live`. Its existing launcher is
`.cardine-ui-preview/merged-main-server/launch.py` in the durable Desktop/Dev
checkout. Confirm no active generation/turn, retain verified SQLite backups,
configuration, student journal, launcher and version metadata, then fast-forward
the server checkout and restart using that launcher. Verify the running commit,
HTTP health, model configuration, READY indexing and unchanged canonical events.

Runtime-only OpenAI credentials clear on restart and must be entered again in
Settings. Do not extract process memory or persist the key. Keep inherited
provider environment bindings without printing their values. No paid model
call, canonical source migration or production retrieval policy change is part
of this authorized update. Report the final merge/runtime commit, backup path,
CI/review evidence and credential availability to the owner after activation.
