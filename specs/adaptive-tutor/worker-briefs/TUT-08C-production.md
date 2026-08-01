# Worker Brief: TUT-08C reference runtime, artifacts, materials, and context

Date: 2026-07-30
Area: adaptive tutor / Cardine product shell
Bead: `TUT-08C-reference-runtime-artifacts-context.md`
Profile: `cardine-product-slice`

## Goal

Make Cardine's grounded completion, canonical materials, artifact proposal
decisions, and intrinsic study-context conflict flow truthful and live over
`LocalRepository`, following ADR-0016.

## Allowed Files

- `src/study_agent/application/capability_completion.py` (new)
- `src/study_agent/application/reference_product.py` (new if useful)
- exports immediately adjacent to those modules
- `src/study_agent/hosts/contracts.py`
- `src/study_agent/hosts/runner.py`
- `src/study_agent/cli/repository.py`
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- focused TUT-08C unit/integration/E2E tests
- the TUT-08C bead and one factual dev log

## Forbidden Scope

- Assessment/evidence mutation, recall, Today, or exam-readiness implementation.
- New external dependencies.
- Changes to the seven public StudyTool/capability schemas.
- Raw provider output, prompts, source text, filesystem paths, expected answers,
  worker scratch, or credentials in browser DTOs.
- Automatic artifact acceptance.
- Replacing existing artifact, source, or study-context owners.
- Editing unrelated UI polish files.

## Required Invariants

- Follow ADR-0016: a completed host capability may expose only the closed,
  verified completion reference; a private identity/version handler must
  recover owner output before any canonical product effect or tutor message.
- Unknown or unrecoverable completions are status-only and commit no product
  effect.
- For `explain_concept@1`, render only locally recovered verified grounded
  explanation segments/citations; never generic `completed_output`.
- Generated artifacts remain proposals. Accept/reject route through
  `ArtifactService`, are course/session scoped, exact-retry safe, and map the
  browser's `accepted/rejected` values explicitly to domain commands.
- Artifact lists filter course-wide projection rows to the selected session.
- Materials use canonical catalog/revision metadata only.
- Intrinsic context conflicts come from `ProjectionStudyContextView`; candidates
  expose opaque `StatementId` plus bounded display/provenance. Resolution sends
  `selected_statement_id` to `StudyContextService`.
- Source disagreements remain read-only.
- Public-demo mode cannot mutate repository state.

## Acceptance Criteria

1. A real grounded `explain_concept` completion produces a canonical tutor
   presentation after verified owner recovery and survives reload/exact retry.
2. A verified artifact-generation completion can commit proposed revisions,
   list them after restart, and never self-accept. If the full generation
   runtime cannot be composed without inventing a production owner, leave that
   capability undiscoverable and document the exact missing owner; do not fake
   output.
3. Existing proposed revisions can be accepted/rejected individually through
   Cardine with stale/ownership/idempotency tests.
4. Material DTOs expose real revision/kind/checksum/chunk/provenance metadata.
5. Intrinsic context conflicts expose `statement_id`; resolving one candidate
   survives reload and rejects value-based or cross-course selection.
6. All TUT-08C routes have ready/empty/stale/error/redaction coverage.

## Verification

- Focused artifact lifecycle, verified-batch, context, repository-backed UI,
  and real loopback HTTP tests.
- Existing conversation/host regressions.
- `python -m ruff check` on changed files.
- `git diff --check`.

## Coordination

You are not alone in the codebase. Do not revert or overwrite other agents'
changes. Preserve the completed TUT-08A/B implementation and adapt to the
current dirty worktree. Do not recursively delegate.
