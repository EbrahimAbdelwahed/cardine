# Worker Brief: TUT-08F Today and exam readiness

Date: 2026-07-30
Area: adaptive tutor / Cardine product shell
Bead: `TUT-08F-today-and-exam-readiness.md`
Profile: `cardine-product-slice`

## Goal

Build ADR-0017's projection-only readiness view and use it to populate Cardine
Today, Piano, feature flags, and honest counts without inventing a plan or score.

## Allowed Files

- `src/study_agent/application/study_readiness.py` (new)
- application/composition exports and reference-product composition
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js` and narrowly required Cardine CSS
- focused TUT-08F unit/integration/E2E tests
- the TUT-08F bead and one factual dev log

## Forbidden Scope

- New events, persistence, mutations, model calls, recommendations, agendas, or
  next-capability selection.
- Mastery, retention, readiness, coverage, predicted-score, or invented
  study-time percentages.
- Browser date/count calculations.
- New dependencies or unrelated UI redesign.

## Required Invariants

- Implement ADR-0017 exactly as a pure read model over one immutable projection
  capture/high-water mark and an injected aware UTC clock.
- Expose configured exam date, exact `as_of_date`, raw calendar days remaining
  (including negative past dates), learning goals, and assessment styles with
  source attribution.
- Missing or conflicting deadline yields explicit status and no numeric days;
  never choose silently or use browser/local time.
- Expose active learner deadline/weekly-budget constraints or conflicts from
  `ProjectionStudyContextView`, preserving configured-vs-learner attribution.
- Expose accepted exam-blueprint observations/limitations as observations,
  never as coverage/readiness.
- Expose pending/accepted artifact counts by kind, exact learner-evidence
  ratios/references, and due count/earliest due time only when recall read state
  is available.
- Every field/row identifies its source projection and sequence.
- Shell warning precedence may use context conflicts or due work while keeping
  an active session active; optional-owner absence is not a global error.
- Public demo stays immutable/sanitized.

## Acceptance Criteria

1. Exact date boundaries, past dates, missing dates, naive-clock rejection, and
   conflicts are deterministic and tested.
2. Sparse repositories return explicit empty/missing states, never estimates.
3. Blueprint rows, artifact counts, evidence ratios, context conflicts, and
   recall counts are attributed to canonical projections/sequences.
4. Today and Piano render useful facts/constraints/open work; the browser does
   no date math and the static “no owner” plan is removed.
5. Bootstrap flags/counts reflect the actually composed owners.
6. Replay/restart with the same clock yields byte-equivalent DTOs and no writes
   or model calls.

## Verification

- Pure readiness unit tests with fake clock/one-projection inputs.
- Repository-backed Today/Piano/bootstrap restart and conflict tests.
- Real loopback HTTP and browser contract tests.
- `python -m ruff check` on changed files and `git diff --check`.

## Coordination

You are not alone in the codebase. Do not revert other agents' work. Start only
after TUT-08C/D/E DTO owners are stable and do not recursively delegate.
