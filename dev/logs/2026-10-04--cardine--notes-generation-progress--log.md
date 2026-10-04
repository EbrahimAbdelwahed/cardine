# Visible study-note progress

Owner request: use PageIndex to divide the source without manual index entry,
and show segmentation/generation progress for each lesson.

Checkout: Codex managed `1603/cardine`, branch `codex/notes-generation-progress`.
Dependency/base: PR #18 `codex/lesson-structure-picker`, commit `a630285`.
That PR already supplies automatic ready PageIndex lesson/section selection;
the current local server is on main and does not include it. This stacked change
adds truthful progress without duplicating the picker or claiming live rollout.

Added active lease stage, verified segment totals/titles and safe progress-warning
fields to the existing status API. The browser shows the current lesson/segment,
completed segment counts and an accessible progress bar. Successful generation
focuses the progress panel; resume is limited to retry states.

An offline browser regression failed before implementation, then passed with the
model held at segmentation and segment generation. Existing PageIndex/PDF notes,
approval and publication journeys passed (63 focused cases); added product coverage
checks reopened counts and rejected manifest mismatch without canonical writes.

Visual critique found confusing current/completed counts and sparse queue copy;
both were clarified. Initial mobile capture caught the viewport transition
mid-animation, so final captures wait for settling and show unobstructed content.
The large reader panel/notes position remains a broader Sources layout issue.
A fresh image-only critique confirmed readable text and no mobile clipping or
obstruction. The redundant segment title was omitted when identical to the lesson.
The gray empty progress track is accompanied by explicit completed counts; the
source action remains available to select another lesson from that source.

Full first suite: 2,937 passed, four optional skips, two failures. The new archive
custody test needed its overlay committed; the accent marker conflicted with the
existing design-system rule, corrected by using bar backgrounds only.
Ruff and strict mypy (671 files), isolated wheel/sdist build and custody audit
passed. Final post-commit checks determine readiness.

The archive regressions also require historical manifest edits to fail even
when a newer product overlay supersedes a runtime row. Their three immutable
manifest digests are now checked separately from the latest runtime bindings.
Historical files themselves are unchanged. PR #21 is stacked on PR #18.

No paid provider calls, live source writes, restart, merge or deployment.
Automatic GitHub semantic review is limited to two rounds. Local image critique
does not replace that review. PR #20 owns the independent flashcard/context repair.

Final runtime commit `004075d`: all 26 post-commit archive/design/browser checks
passed, covering both originally failing gates. Additional 34 design/product
checks and 63 PageIndex/PDF/browser cases passed. Ruff, strict mypy, isolated
build and custody audit passed. PR #21 is ready for automatic review, with
current-head GitHub CI/review pending. No unpublished runtime changes remain.

First automatic review (`0e9e010`): one P2 finding, oldest-job focus after a new
submission. Reproduced with an existing proposed job at the real browser seam.
Both submission flows now use the returned job ID; no fallback selects unrelated
historical progress. Empty-history and existing-job progress cases pass, together
with the existing PDF/PageIndex browser journeys (nine cases). The final focused
progress recheck passes both scenarios. Ruff, mypy, isolated build and custody
checks pass. Only a second final automatic review is authorized for this PR.

Owner continuation, 2026-10-04: “termina il lavoro” explicitly authorizes fixing
the remaining second-round finding without requesting another automatic review.
A mutually consistent foreign manifest/boundaries pair was reproduced in four
offline cases (foreign text, digest, length and unit content). Status now binds
all manifest units, fingerprint and length to the exact pinned transcript before
exposing segment details; invalid progress remains read-only and unavailable.
No additional automatic review, merge, deployment or paid calls are requested.

Final owner-continuation verification: 2,932 offline tests passed, four optional
skips (clean-archive tests run separately after commit); all 25 product tests
passed. Ruff, mypy (671 files), isolated wheel/sdist and custody audit passed.
Previous-head CI had one Python 3.13 browser failure: the test clicked the static
rail before initial UI readiness. The two relevant journeys now wait for the
loaded bootstrap consent control before navigation; final browser/archive checks are recorded
in the PR. Current submitted-commit CI remains required.

Composer readiness was too narrow for an unfinished study setup: those fixture
courses render onboarding instead of a composer. The readiness check uses the
bootstrap-rendered consent control, shared by both onboarding and normal study.

Final browser/clean-archive recheck: all 17 cases passed in 64.55 seconds,
including the previously failing CI journey and both held-generation histories.
All known note-progress review findings are fixed; no further semantic review
was requested after the owner's continuation. Dependency #18, current-head CI
and explicit merge authorization remain delivery gates.
