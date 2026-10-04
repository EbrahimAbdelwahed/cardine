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

No paid provider calls, live source writes, restart, merge or deployment.
Automatic GitHub semantic review is limited to two rounds. Local image critique
does not replace that review. PR #20 owns the independent flashcard/context repair.
