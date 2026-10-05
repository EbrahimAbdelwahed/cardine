# UI/UX redesign — phase 2 (exam plan and onboarding)

Date: 2026-10-05
Branch: `claude/ui-phase2-study-plan`, stacked on PR #29
Spec: [`specs/ui-ux-redesign/README.md`](../../specs/ui-ux-redesign/README.md), ADR-0027

## Outcome

- Canonical `course.study_plan_set@1` event (exam date, daily minutes,
  objective) with codec, reducer, `ProjectionCourseView.study_plan` and
  `CourseService.set_study_plan`; `POST /api/v1/plan`.
- `cardine.application.study_schedule`: pure outline + schedule over verified
  PageIndex spans and the student journal; recomputed on every read.
- `GET /api/v1/plan/schedule` (outline + schedule) separate from the fast plan
  header; bootstrap `today` (today's lessons, countdown, studied/total).
  Materials rows carry `structure_status` for visible background processing.
- Browser: three-step onboarding (exam, sources with dropzone and live
  processing, plan preview); home "Oggi" card; Piano with countdown, progress,
  today, grouped timeline (practice and final-review stretches) and lesson
  outline; shared lesson actions (Studia, Genera note, Segna studiata).

## Design decisions made while building

- The first schedule put 21 of 22 days on "review" for a one-lesson course.
  Lessons are now spaced across the period with practice days between them,
  and consecutive practice/review days collapse into one timeline row.
- A small source with one subsection used to become that subsection's title.
  A lone wrapper is unwrapped only when it holds three or more chapters.
- Onboarding interrupts only a fresh course (no learner turn yet); a course in
  use sees a discreet "Imposta la data d'esame" row.
- `/api/v1/plan` first decoded every source text for the outline and missed the
  Piano page-load budget (965 ms > 783 ms). The outline now reads the canonical
  projection (origin, normalized length) and the PageIndex statuses, and loads
  after the header; budgets pass again.

## Custody

`tests/parity/ui-redesign-overlay.json` (ADR-0027) now binds the browser
assets, `ui_application.py`, `cli/repository.py` and `cardine/courses/*`. The
superseded notes-progress manifest is pinned by digest. PR #29 received the
same overlay for its three browser assets so its clean-archive audit passes.

## Verification

- New: study plan contract (6), schedule unit (11), plan integration (5).
- Full `pytest`, `ruff`, `mypy`, ownership audit; real-browser e2e journeys
  and page-load budgets, updated for the exam-first onboarding.
- Visual review on a synthetic seeded course: onboarding, home, plan
  (light/dark, desktop and 375 px mobile).
