# Task Bead: TUT-08C reference runtime, materials, artifacts, and context

Status: Done
Priority: P0
Type: product tracer-bullet
Depends On: TUT-08A, TUT-08B, ADR-0016

Child Beads:

- `TUT-08C1-grounded-completion-handoff.md` — Done
- `TUT-08C2-materials-artifacts-context.md` — Done

## Outcome

The reference repository composition exposes truthful material provenance,
capability-generated artifact proposals, individual human decisions, intrinsic
study-context conflicts, and `StatementId`-bound resolution through Cardine.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- FP-03 grounded artifact lifecycle.
- FP-06 context resolution.
- FP-07 initial real feature counts.
- FP-08 source/provenance inspection.
- FP-10 flow-specific states and restart behavior.

## Grilling Evidence

- Session/artifact:
  - `specs/adaptive-tutor/cardine-full-product.md`
  - `docs/decisions/ADR-0016--closed-capability-completion-handoff.md`
  - 2026-07-30 architecture audit
- Decision state: approved
- ADR/glossary changes: ADR-0016; none additional

## Worker Profile

reuse `cardine-product-slice`

Rationale:

These controls share the same product application/repository boundary and can
be proven with one grounded proposal-to-decision tracer without changing their
existing owners.

## Context

Artifact events are registered but `LocalRepository` does not expose the full
artifact view/service/runtime. Cardine returns unavailable proposal state and
maps only tutor-profile divergences instead of intrinsic
`StudyContextSnapshot.conflicts`. The browser currently submits a displayed
value rather than a canonical `StatementId`.

## What To Do

- Introduce or extend a deep private reference-product composition that owns
  repository views/services but exposes only typed application methods.
- Compose `ProjectionTutorPresentationView`, `ProjectionArtifactView`,
  `ArtifactService`, verified generated-batch recovery, source commitments,
  decision policy, and the closed completion-handler registry.
- Route supported grounded artifact completions through ADR-0016 to verified
  recovery and proposal commit; unknown completions remain status-only.
- Implement artifact GET DTOs and individual accept/reject POST commands.
- Never expose expected answers, raw prompts, worker scratch, provider output,
  filesystem paths, or unbounded provenance.
- Implement real material DTOs from canonical source revisions.
- Read intrinsic study-context conflicts and expose each active candidate's
  opaque `statement_id` plus display value and provenance.
- Resolve conflicts using `selected_statement_id`, not displayed text.
- Keep source disagreements read-only.
- Activate truthful bootstrap feature flags and counts for the completed
  owners.
- Add public-demo isolation and real repository restart/race/E2E tests.

## Likely Files / Packages

- `src/study_agent/application/reference_product.py`
- `src/study_agent/application/capability_completion.py`
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- focused artifact/context/material application, integration, and E2E tests

## Acceptance Criteria

- [ ] A verified grounded capability produces canonical proposed artifact
  revisions and Cardine lists them after restart.
- [ ] Generated proposals never self-accept.
- [ ] Accept and reject use exact retries, reject changed identities, enforce
  course/session ownership, and fail stale before new work.
- [ ] Material rows expose real revision/kind/checksum/chunk/provenance metadata.
- [ ] Context conflicts expose canonical candidate `StatementId` values.
- [ ] Selecting one candidate resolves the conflict through
  `StudyContextService` and survives reload.
- [ ] Source disagreements remain read-only.
- [ ] Public demo cannot execute repository mutations.

## Verification

- focused artifact lifecycle/headless generation/context integration tests
- repository-backed Cardine proposal/decision/conflict tests
- browser contract for controls, redaction, empty/error/stale/reload
- Ruff, mypy when available, and `git diff --check`

## Out Of Scope

- Assessment, recall, and exam-readiness behavior.
- Source upload or filesystem browsing from Cardine.
- Automatic artifact acceptance.

## Notes / Handoff

- Existing “Done” TUT-04 contracts are reused, not redesigned.
