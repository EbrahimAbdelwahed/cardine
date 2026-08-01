# ADR-0017: Derived exam-readiness view instead of a canonical planner

Date: 2026-07-30
Status: Accepted

## Context

Cardine exposes a visible “Piano” section, but the harness has no canonical
agenda, coverage, retention, mastery, or exam-readiness owner. Creating a
model-authored plan or browser-computed score would fabricate product truth,
duplicate the tutor's next-action responsibility under ADR-0004, and introduce
new persistence without a defined command lifecycle.

Existing canonical projections already contain useful, attributable planning
inputs: configured exam date and goals, study-context statements and conflicts,
accepted exam blueprints, artifact decisions, assessment evidence, and recall
due state.

## Decision

- Implement `StudyReadinessView` as a projection-only application read model.
  It does not append events and has no mutation route.
- The view contains only:
  - configured exam date and injected-clock `as_of_date`;
  - exact calendar days remaining when one unconflicted date exists;
  - configured learning goals and assessment styles;
  - active learner deadline/budget statements or an explicit conflict marker;
  - accepted exam-blueprint observations and their stated limitations;
  - pending and accepted artifact counts by kind;
  - learner-evidence estimates with canonical references and through-sequence;
  - due-review count and earliest due time.
- Every field names its source projection and sequence. Missing or conflicted
  inputs remain missing/conflicted.
- The view never:
  - persists a plan;
  - generates or prioritizes an agenda;
  - recommends the tutor's next capability;
  - infers mastery, retention, readiness, coverage, or study time;
  - predicts an exam score;
  - calls a model.
- Days remaining is a pure calendar difference using an injected clock; the
  browser never computes it.
- “Piano” renders this view as attributable facts, constraints, and open work,
  not as a readiness score.

## Consequences

- The visible section becomes useful without inventing a new canonical domain.
- Reload/replay equivalence follows the underlying projections and injected
  clock.
- Future canonical scheduling or learner-authored planning requires a separate
  ADR, command owner, events, and migration plan.
- ADR-0004 remains intact: adaptive next-action selection belongs to the host.

## Alternatives Considered

- Persist a model-generated weekly agenda: rejected because prompts and model
  output would become unowned canonical state.
- Compute a single readiness percentage: rejected because the evidence model
  explicitly avoids generic mastery claims.
- Hide the section: rejected because truthful existing inputs can provide a
  useful bounded view.
- Calculate dates/counts in JavaScript: rejected because browser state is not a
  canonical or replayable owner.
