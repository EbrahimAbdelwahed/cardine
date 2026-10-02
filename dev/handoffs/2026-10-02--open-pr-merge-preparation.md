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
