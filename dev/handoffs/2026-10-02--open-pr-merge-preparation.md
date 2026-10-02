# Open PR merge preparation

Owner requested merging all open Cardine PRs on 2026-10-02.

PR #10 at 8014ec0 integrates fetched main. Conflicts preserve recovery behavior,
source provenance, clarification recovery, lesson aliases and both integrity test sets.
The automatic Codex P1 is fixed: an unavailable SHADOW index returns the original
flashcard lesson unit; ON still fails explicitly. The regression and flashcard
journeys pass (20 tests). Ruff and strict mypy (663 files) pass.

No GitHub merge or PR-head push has occurred. Current-head CI and follow-up
Codex review are still required. The ownership audit rejects the integrated bytes
of flashcard_proposals.py and repository.py. Automatic approval review rejected
updating their recovery commitments as beyond generic merge authorization.
The exact two-path change is an unapplied proposal:
../proposals/2026-10-02--pr10-merge-custody.patch. Existing recovery manifest
is unchanged. Historical manifests and future drift checks must remain binding.

PR #12 at c6cc451 remains draft, targets the already merged PR #5 branch,
has failed Python custody checks and one failed native AnyDoc job, and has no
Codex review. Its existing unapplied 25-path/two-removal custody proposal is
at dev/proposals/2026-10-01--student-journal-custody.patch on that branch.
Applying either custody proposal requires explicit owner authorization after
the automatic approval rejection. Retarget #12 to main and integrate current
main (including #10 once merged), then rebind only its authorized journal scope,
verify mutation rejection and rerun CI/review before merge.

Work is in the managed merge-open-prs/cardine checkout. The owner's dirty
codex/notes-generation checkout was not edited.

## Final local verification before custody approval

Full offline suite: 2842 passed, 4 skipped, 2 failed (128.53s). The first
failure is the expected unchanged recovery-custody gate. The second was the PR's
old ToolGateway test fixture: main now supplies trusted snapshot sequence and
continues the decision loop after tool completion. The fixture now checks that
sequence, then an explicit router-selected assistant answer, and never trusts
the tool's high_water_sequence as authoritative. All 47 routing tests pass after
this test-only repair; the 20 study-semantics/flashcard tests already pass.
Ruff and strict mypy pass. Wheel/sdist build and verify_cardine_wheel pass.
AnyDoc CI #12's native timeout test observed pdf_worker_unavailable rather than
pdf_timeout; it requires reassessment on the updated PR head.

Both PRs are still open and unmerged. The exact custody proposals remain unapplied.
The owner was asked for explicit approval for both scopes; no reply yet.

## Explicit custody approval

The owner approved both custody changes with "ok vai" on 2026-10-02.
The PR #10 two-path patch is applied. repository.py is also bound by the newer
source-study-notes overlay, so its duplicate effective commitment uses the same
approved digest. No additional source path or permissive audit exemption is added.
