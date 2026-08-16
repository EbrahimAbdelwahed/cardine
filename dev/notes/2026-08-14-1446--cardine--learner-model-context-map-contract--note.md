# Note: learner-model Context Map contract

Date: 2026-08-14 14:46 CEST
Area: Cardine / learner model

## Context

The target behavior previously described by the user is preserved in the
separate Cardine domain-model checkout:

- `/Users/ebrahimabdelwahed/Desktop/Dev/cardine/CONTEXT-MAP.it.md`
- `/Users/ebrahimabdelwahed/Desktop/Dev/cardine/docs/domain/learner-model/CONTEXT.it.md`
- `/Users/ebrahimabdelwahed/Desktop/Dev/cardine/docs/domain/GAPS.it.md`
- `/Users/ebrahimabdelwahed/Desktop/Dev/cardine/docs/decisions/ADR-0021--separate-factual-ledgers-from-learner-estimates.md`
- `/Users/ebrahimabdelwahed/Desktop/Dev/cardine/docs/decisions/ADR-0022--versioned-two-axis-curriculum-graph.md`

The target Learner Model is currently a domain gap. It consumes attributable
facts from assessments, qualified tutoring observations, and recall, then
derives temporal estimates. The agent must not persist an invented mastery or
readiness value directly.

The approved outputs are:

- multidimensional mastery for `curriculum concept × learning objective`;
- exam-profile-relative readiness;
- coverage and confidence;
- critical gaps;
- an explicit insufficient-evidence state distinct from low performance.

Every estimate must name its policy version, time basis, evidence provenance,
coverage, and confidence. Attempts, grades, recall reviews, assistance
conditions, and schedules remain factual history and are never rewritten by a
new estimate.

## Current Implementation

- Assessment learner evidence is already replayable and attributable, but is
  grouped only by assessment format and literal rubric criterion.
- Recall already records rating, latency, optional confidence, occurrence time,
  and applied scheduling decisions, but does not own mastery.
- The facts-only `StudyReadinessView` intentionally does not calculate mastery,
  retention, coverage, or readiness.
- Progressive study context already stores explicit learner objectives,
  deadlines, weekly time budgets, assessment formats, and testing preferences.
  The agent can currently read only aggregate context counts through
  `context.get`; no private tool exposes the existing typed record operation.
- No trusted versioned curriculum concept/objective graph or approved
  assessment/source alignment exists in this checkout, so a real
  concept-by-objective mastery estimate cannot yet be produced truthfully.

## Implication

Treat two different needs separately:

1. Explicit learner facts and preferences may be recorded through the existing
   progressive study-context owner, with the current human interaction bound as
   provenance. These are not learning metrics.
2. Performance metrics require a separate Learner Model owner. The agent may
   contribute qualified observations, but only the versioned projection derives
   mastery/readiness, and only from approved concept/objective alignments.

The smallest honest Learner Model slice should prove one end-to-end estimate
from an existing assessment or recall fact, retain the exact evidence
references, emit insufficient evidence when the alignment/evidence is absent,
and never let the model author the numeric estimate directly.

## References

- `specs/adaptive-tutor/README.md`: original Context Map and canonical/derived/
  operational state separation in this checkout.
- `specs/adaptive-tutor/beads/TUT-05E-learner-evidence-projection.md`: current
  factual learner-evidence projection and explicit no-mastery boundary.
- `src/study_agent/assessments/evidence.py`: current assessment evidence.
- `src/cardine/application/study_readiness.py`: current facts-only readiness
  view.
- `src/cardine/domain/study_context.py`: existing closed learner-context
  vocabulary.
