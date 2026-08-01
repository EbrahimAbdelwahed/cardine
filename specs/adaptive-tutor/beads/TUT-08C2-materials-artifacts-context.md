# Task Bead: TUT-08C2 materials, artifacts, and context

Status: Done
Priority: P0
Type: product tracer-bullet
Depends On: TUT-08C1

## Outcome

Cardine exposes canonical material metadata, selected-session artifact
proposals and decisions, and intrinsic study-context conflict resolution using
opaque `StatementId` candidates.

## What To Do

- Compose `ProjectionArtifactView` and `ArtifactService` in the reference product.
- List selected-session proposals/accepted/rejected revisions without answer,
  raw-output, worker, path, or source-text leakage.
- Route individual accept/reject commands with exact retry, ownership, and stale fences.
- Map browser `accepted/rejected` explicitly to domain `accept/reject`.
- Populate materials from canonical source catalog/revision metadata.
- Read `ProjectionStudyContextView.conflicts`, expose each candidate's
  `statement_id`, bounded value and provenance, and resolve with
  `selected_statement_id`.
- Keep source disagreements read-only.
- Activate only truthful feature flags/counts and preserve public-demo immutability.

## Acceptance Criteria

- [x] Material rows expose revision/kind/checksum/chunk/provenance metadata.
- [x] Proposed revisions remain proposals and filter to the selected session.
- [x] Accept/reject survives restart and exact retry without duplicate events.
- [x] Stale/cross-session/cross-course artifact commands commit nothing.
- [x] Intrinsic conflicts expose canonical candidate `StatementId` values.
- [x] Selecting one candidate resolves through `StudyContextService` and survives reload.
- [x] Display values cannot be submitted in place of `StatementId`.
- [x] Public demo cannot execute repository mutations.

## Verification

- Focused artifact lifecycle and study-context integration tests.
- Repository-backed Cardine HTTP/reload/redaction tests.
- Ruff and `git diff --check`.

## Out Of Scope

- Inventing a production lesson/exam generation owner.
- Assessment, recall, Today, or exam-readiness behavior.
