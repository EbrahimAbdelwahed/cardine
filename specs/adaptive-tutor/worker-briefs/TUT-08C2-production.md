# Worker Brief: TUT-08C2 materials, artifacts, and context

Date: 2026-07-30
Area: adaptive tutor / Cardine product shell
Bead: `TUT-08C2-materials-artifacts-context.md`
Profile: `cardine-product-slice`

## Goal

Compose existing canonical material, artifact-lifecycle, and study-context
owners into the private Cardine product API without inventing generation.

## Allowed Files

- reference-product application/composition modules
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- narrowly required artifact/study-context composition exports
- focused TUT-08C2 unit/integration/E2E tests
- C/C2 beads and one factual log

## Forbidden Scope

- Capability/lesson/exam generation without an existing production owner.
- Assessment, recall, Today, or exam-readiness implementation.
- New dependencies or changes to artifact/context event contracts.
- Raw source text, expected answers, filesystem paths, provider output,
  prompts, worker scratch/proof, or credentials in DTOs.
- Automatic artifact acceptance or value-based context resolution.

## Required Invariants

- Reuse `ProjectionArtifactView`, `ArtifactService`,
  `ProjectionStudyContextView`, `StudyContextService`, and canonical source
  catalog/revision views.
- Filter artifact rows through batch `session_id` for the selected session.
- Map browser `accepted/rejected` explicitly to domain `accept/reject`.
- Accept/reject is HUMAN, sequence-fenced, exact-retry safe, and names the
  current accepted predecessor when required.
- Context GET uses intrinsic `.conflicts`; candidate DTOs include opaque
  `statement_id`, bounded display value, and provenance.
- Context POST accepts only `selected_statement_id`; cross-course, stale,
  unknown, display-value, and source-disagreement mutations fail closed.
- Public-demo mode cannot mutate.
- Truthful empty/unavailable/error states and feature flags only.

## Acceptance Criteria and Verification

Use every criterion and command from the C2 bead. Run focused existing artifact
and context tests, new repository-backed Cardine loopback/reload/redaction
tests, Ruff, and `git diff --check`.

## Coordination

You are not alone in the codebase. Do not revert C1 or review fixes, do not
touch forbidden beads, and do not recursively delegate.
