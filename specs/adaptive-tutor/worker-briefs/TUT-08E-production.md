# Worker Brief: TUT-08E recall review flow

Date: 2026-07-30
Area: adaptive tutor / Cardine product shell
Bead: `TUT-08E-recall-review-flow.md`
Profile: `cardine-product-slice`

## Goal

Expose the existing canonical recall ledger in Cardine and make accepted
flashcard enrollment a separate resumable step exactly as required by ADR-0016.

## Allowed Files

- reference-product application/composition modules from TUT-08C/D
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- narrowly required recall composition exports
- focused TUT-08E unit/integration/E2E tests
- the TUT-08E bead and one factual dev log

## Forbidden Scope

- Changing recall events, scheduling formulas, or artifact acceptance semantics.
- Importing optional FSRS at core module import time.
- Browser-computed schedules or local canonical review state.
- Rolling back/concealing artifact acceptance when enrollment fails.
- New dependencies, generic mastery metrics, assessment, Today, or exam-plan work.
- Exposing full source text, filesystem paths, provider output, prompts, or credentials.

## Required Invariants

- Reuse `compose_recall`, `RecallService`, `ProjectionRecallView`,
  `DueRecallView`, and the configured scheduling port.
- Artifact acceptance commits first. If the accepted revision is a flashcard,
  enrollment is a separate SERVICE command with a stable domain-separated
  identity derived from course/session/revision.
- Exact recovery checks the accepted artifact and enrollment independently.
  Enrollment failure remains visible and retryable; later exact retry schedules
  once and never duplicates the artifact decision.
- GET due reads one canonical projection high-water mark, filters accepted
  current flashcard revisions, and joins only bounded learner-safe content and
  provenance.
- Review maps exactly `again|hard|good|easy` to `RecallRating`, is HUMAN,
  expected-sequence fenced, and delegates schedule calculation to the server
  policy. One review and its schedule append atomically.
- Exact retry does not rerun the scheduler. Stale/cross-course/invalid target
  commits nothing.
- Later study sessions may review course-owned due cards as allowed by the
  existing service.
- If no scheduler is configured, report honest `not_configured`/unavailable
  states; never fabricate dates.
- Public-demo mode cannot mutate repository state.

## Acceptance Criteria

1. Accepted flashcard -> separate enrollment -> due row survives restart.
2. Enrollment failure leaves acceptance intact and exact retry resumes without
   duplicate events.
3. Card front/back/provenance are bounded and current; superseded cards vanish.
4. Reveal stays local presentation state; all four ratings invoke canonical
   commands and refresh the due list.
5. Review/schedule is atomic, exact-retry safe, and stale/cross-target safe.
6. Configured/unconfigured/factory-failure states are explicit and tested.

## Verification

- Existing recall composition/service/replay tests.
- Repository-backed Cardine enrollment/due/review/reload tests using a
  deterministic fake scheduler in the base environment.
- Optional real-FSRS lane only when installed.
- Real loopback HTTP and browser contract tests.
- `python -m ruff check` on changed files and `git diff --check`.

## Coordination

You are not alone in the codebase. Do not revert other agents' work. Start only
after the artifact decision seam from TUT-08C is stable and do not recursively
delegate.
