# Task Bead: TUT-08F Today aggregation and exam readiness

Status: Complete
Priority: P1
Type: product tracer-bullet
Depends On: TUT-08C, TUT-08D, TUT-08E, ADR-0017

## Outcome

Cardine “Oggi” and “Piano” display deterministic, attributable repository
facts from the completed product owners without persisting a plan or inventing
readiness metrics.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- FP-07 real feature availability and counts.
- FP-09 truthful exam-readiness view.
- FP-10 Today/plan sparse/conflict/reload states.

## Grilling Evidence

- Session/artifact:
  - `docs/decisions/ADR-0017--derived-exam-readiness-view.md`
  - `specs/adaptive-tutor/cardine-full-product.md`
- Decision state: approved
- ADR/glossary changes: ADR-0017; none additional

## Worker Profile

reuse `cardine-product-slice`

Rationale:

This is a projection-only product view over owners already activated by prior
beads.

## Context

Bootstrap counts are currently hardcoded and Piano is a local unavailable
screen. Existing canonical projections contain explicit planning inputs but no
agenda or mastery owner.

## What To Do

- Implement `StudyReadinessView` exactly as ADR-0017.
- Use an injected clock for `as_of_date` and days remaining.
- Include source projection/sequence attribution for every row.
- Represent missing exam date, conflicting deadline, sparse evidence,
  no-blueprint, and optional recall absence explicitly.
- Replace hardcoded bootstrap feature flags/counts with real projection values.
- Make shell status reflect pending review/context conflict without claiming
  global failure.
- Bind `/api/v1/plan` and render attributable constraints/open work in Cardine.
- Keep browser calculation limited to presentation only.

## Likely Files / Packages

- `src/study_agent/application/study_readiness.py`
- reference product/application composition
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- focused readiness/bootstrap/browser tests

## Acceptance Criteria

- [x] Exact days remaining uses configured exam date and injected clock.
- [x] Conflicting/missing deadlines never produce a number.
- [x] Counts match artifact, assessment, recall, and context projections.
- [x] Evidence and blueprint observations retain references and limitations.
- [x] No mastery, retention, coverage, agenda, priority, or predicted score is
  emitted.
- [x] Equivalent replay state and clock produce byte-equivalent DTOs.
- [x] Public demo remains sanitized and labels unavailable owners honestly.

## Verification

- deterministic clock/projection/replay tests
- sparse/conflict/no-blueprint/optional-recall cases
- Cardine Today/Piano desktop/mobile browser checks
- architecture assertion that readiness performs no writes/model calls
- Ruff, mypy when available, and `git diff --check`

## Out Of Scope

- Canonical plan mutations, model-authored agendas, or next-action selection.

## Notes / Handoff

- A future planner requires a new ADR and owner.
