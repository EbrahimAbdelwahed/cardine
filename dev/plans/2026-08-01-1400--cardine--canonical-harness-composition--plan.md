# Plan: Cardine canonical Harness composition

Date: 2026-08-01 14:00
Area: Cardine / Study Agent Harness

## Goal

Remove the stateless browser-demo composition and make Cardine a private,
repository-backed client of a single canonical Harness tool composition.

## Scope

- In scope: repository-only browser construction, removal of public-demo
  fallback/routes, shared tool composition surfaced to UI and tutor context,
  source-first setup commands through canonical services, and E2E proof.
- Out of scope: provider-specific function-calling protocol changes. The tutor
  decision boundary stays provider-neutral and receives typed manifests.

## Approach

1. Collapse browser construction onto `RepositoryUiApplication`; reject the
   stateless/server fallback at the process composition root.
2. Introduce a deep, typed `HarnessToolSurface` module composed once from a
   `LocalRepository`; use it both for Cardine mutations and to advertise the
   same tool manifests to the tutor decision context.
3. Extend the closed public tool registry with canonical adapters for source
   ingestion, workspace lifecycle, learner context, recall, artifacts,
   assessment, and evidence reads. Keep execution authority in existing
   application services.
4. Make setup a recorded sequence of source-first tool actions and validate
   repository timeline/presentation output end-to-end.

## Risks

- Existing tests intentionally freeze the old seven-tool public vocabulary;
  they must be updated together with the canonical surface rather than bypassed.
- Course profile mutation is intentionally absent; setup data must use the
  existing StudyContext/session mechanisms until a separate profile-change
  contract is approved.

## Verification

- Focused tool-registry and Cardine transport tests.
- A repository-only browser/API E2E that creates a course/session, ingests a
  source, records setup context, and observes a persisted tutor timeline.
