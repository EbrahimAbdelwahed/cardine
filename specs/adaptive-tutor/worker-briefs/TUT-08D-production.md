# Worker Brief: TUT-08D assessments and learner evidence

Date: 2026-07-30
Area: adaptive tutor / Cardine product shell
Bead: `TUT-08D-assessments-and-evidence.md`
Profile: `cardine-product-slice`

## Goal

Compose the existing canonical assessment ledger and attributable learner
evidence projection into Cardine, with separate attempt and grade commands and
no answer/rubric disclosure.

## Allowed Files

- the reference-product application/composition modules introduced by TUT-08C
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- narrowly required assessment composition exports
- focused TUT-08D unit/integration/E2E tests
- the TUT-08D bead and one factual dev log

## Forbidden Scope

- Changing assessment event/contracts, grading semantics, or evidence formulas.
- New dependencies or direct LLM calls from UI/application DTO mapping.
- Showing expected responses, evaluation criteria/rubrics, provider output,
  prompts, worker proof, source text, or credentials.
- Generic mastery, retention, coverage, or readiness percentages.
- Recall, Today, exam planning, or unrelated UI polish.

## Required Invariants

- Reuse `AssessmentService`, `ProjectionAssessmentView`,
  `ProjectionArtifactView`, `ProjectionLearnerEvidenceView`, and
  `ExactClosedGradingPolicy`; do not create another state owner.
- List only learner-safe accepted assessment presentations for the selected
  course/session.
- Convert a strict browser response union to `SingleChoiceResponse`,
  `MultipleChoiceResponse`, or `FreeResponse`.
- Record an attempt before any grade. Attempt and grade use distinct,
  domain-separated server identities and expected-sequence fences.
- Closed responses grade deterministically without a model.
- Free responses may grade only through an existing verified-grade recovery
  port. If the reference proof runtime cannot be composed truthfully, return a
  bounded `needs_review`/unavailable state and do not fake a grade.
- Exact retry duplicates neither attempt, grade, nor provider work; stale and
  cross-session IDs commit nothing.
- Preserve every prior grade/contest/supersession record while identifying the
  active lifecycle.
- `/api/v1/evidence` maps canonical estimates and bounded references with the
  exact `through_sequence`; it does not reuse tutor-profile context as evidence.
- Public-demo mode cannot execute mutations.

## Acceptance Criteria

1. Accepted closed and free-response items render with accessible choice or
   textarea controls and no expected answer/rubric fields.
2. Attempt commit and grade commit are visibly separate and reload-safe.
3. Closed grading is deterministic and provider-free.
4. Free grading is proof-bound or truthfully unavailable/needs-review.
5. Contest/supersession keeps prior records and shows the active truth.
6. Evidence is canonical, attributable, replay/restart stable, and rendered
   under the DTO key the browser consumes.
7. Stale, duplicate, malformed, and cross-session commands are covered.

## Verification

- Existing assessment service, verified grading, and learner-evidence tests.
- Repository-backed Cardine closed/free attempt/grade/evidence tests.
- Real loopback HTTP and browser contract tests.
- `python -m ruff check` on changed files.
- `git diff --check`.

## Coordination

You are not alone in the codebase. Do not revert other agents' work. Start only
after TUT-08C's composition seam is stable, adapt to it, and do not recursively
delegate.
