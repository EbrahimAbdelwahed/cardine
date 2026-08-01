# Task Bead: TUT-08D assessments and learner evidence

Status: Complete
Priority: P0
Type: product tracer-bullet
Depends On: TUT-08C

## Outcome

Cardine presents accepted assessment items, records closed and free responses,
grades them through existing verified owners, and refreshes attributable
learner evidence after restart.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- FP-04 assessment lifecycle and evidence.
- FP-07 assessment counts.
- FP-10 assessment/evidence states and E2E.

## Grilling Evidence

- Session/artifact:
  - `specs/adaptive-tutor/cardine-full-product.md`
  - `specs/adaptive-tutor/beads/TUT-05-assessment-and-learner-evidence.md`
  - 2026-07-30 architecture audit
- Decision state: approved
- ADR/glossary changes: none

## Worker Profile

reuse `cardine-product-slice`

Rationale:

The assessment ledger and evidence projection already own the behavior; this
bead composes them into one vertical product lifecycle.

## Context

`LocalRepository` exposes assessment/evidence views but not the complete
`AssessmentService` and verified grading runtime. Cardine has choice controls
but no free-response input, and `/evidence` currently returns learner context
under a key the browser does not render.

## What To Do

- Compose `AssessmentService`, artifact view, deterministic closed grading, and
  verified free-text grade recovery in the reference product runtime.
- List presentations, attempts, active grades, contests, and safe lifecycle
  status without disclosing expected answers/rubrics prematurely.
- Convert browser responses to the strict canonical response union.
- Add accessible free-response input and multiple-choice behavior.
- Record attempt before grade; never collapse the two commands.
- Implement deterministic closed grading and verified free-text grading.
- Route `/evidence` to `ProjectionLearnerEvidenceView`, including references and
  exact through-sequence.
- Add real counts and honest empty/needs-review/contested states.

## Likely Files / Packages

- reference-product application/composition modules
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js` and focused CSS if required
- assessment/evidence integration and Cardine E2E tests

## Acceptance Criteria

- [x] Accepted closed and free-response items render without leaking answers.
- [x] Attempt commits before grade and exact retry duplicates neither.
- [x] Closed answers grade without a model.
- [x] Free responses use verified grading and expose graded, needs-review, or
  ungradable truthfully.
- [x] Contest/supersession retains prior grades and shows the active lifecycle.
- [x] Evidence refreshes from `ProjectionLearnerEvidenceView` with references
  and survives replay/restart.
- [x] Stale commands and cross-session IDs commit nothing.

## Verification

- existing TUT-05 unit/integration/verified grading tests
- new reference-product assessment/evidence integration tests
- browser closed/free-response/grade/evidence/reload E2E
- Ruff, mypy when available, and `git diff --check`

## Out Of Scope

- Generic mastery percentages or model-authored readiness.
- Recall scheduling.

## Notes / Handoff

- Expected answers and rubrics remain server-side until lifecycle disclosure is
  explicitly safe.
