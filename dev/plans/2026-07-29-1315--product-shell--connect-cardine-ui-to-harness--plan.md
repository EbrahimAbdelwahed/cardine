# Plan: Connect the Cardine Referto UI to Study Agent Harness

Date: 2026-07-29 13:15
Area: product-shell
Status: Active

## Goal

Materialize `study-agent-ui/Study Agent per Medicina.zip` as a normal,
dependency-free product UI and connect every visible area to the existing
Study Agent Harness application owners. The result must support a safe public
demo deployment and a durable local-repository mode without moving canonical
state, credentials, or policy into the browser.

The selected visual direction is `Cardine Referto.dc.html`. `Cardine.dc.html`
may fill interaction gaps, but it is not permission to invent unsupported
metrics or duplicate domain behavior.

## Scope

- In scope:
  - Referto HTML/CSS/JS assets packaged with the Python distribution.
  - Versioned same-origin JSON routes for bootstrap, session, materials,
    proposals, assessments, evidence, recall, and learner-context conflicts.
  - A repository-backed mode composed only through existing service owners.
  - A deterministic public-demo mode with sanitized fixtures and no secrets.
  - Loading, empty, unavailable, stale, conflict, suspended, retry, and
    degraded states.
  - Production packaging, health checks, environment configuration, and a
    documented deploy path.
- Out of scope:
  - Browser access to SQLite, event stores, filesystem paths, provider
    credentials, execution authority, or capability grants.
  - Fabricated retention, study-time, coverage, agenda, or exam-plan data.
  - Remote writes before authentication and tenancy have an approved owner.
  - A second implementation of tutoring, artifacts, assessments, recall, or
    study context in the HTTP layer.
  - Changes to `sbobby-web`.

## Truth Matrix

| Referto area | Canonical owner | Initial treatment |
| --- | --- | --- |
| Oggi | tutor snapshot, due recall, pending proposals, divergences | Real action counts only |
| Sessione | `SessionTurnService`, `TutorHostRunner`, continuations | Durable tracer first |
| Fonti | tutor/material source projections | Bounded metadata and evidence |
| Proposte | `ProjectionArtifactView`, `ArtifactService` | List and individual decision |
| Ripasso | `DueRecallView`, `RecallService` | Real due queue and four ratings |
| Verifiche | `ProjectionAssessmentView`, `AssessmentService` | Present, attempt, grade |
| Evidenze | `ProjectionLearnerEvidenceView` | Actual criterion estimates |
| Piano | no canonical owner | Explicit unavailable state |
| Conflitti | tutor divergences, `StudyContextService` | Learner-context resolution only |

## Architecture

```text
Referto browser assets
  -> versioned same-origin JSON API
    -> ReferenceUiApplication (validation and orchestration)
      -> existing service/view owners
        -> LocalRepository composition root
          -> append-only per-course event stream
```

The browser sends untrusted text, opaque IDs, an observed high-water sequence,
and a stable request ID. The server owns principal identity, repository
selection, capability grants, idempotency derivation, provider configuration,
and all canonical writes.

Public-demo mode is sanitized and non-secret. Repository mutation remains
localhost-only until an authentication/tenancy boundary is separately
approved.

## Planned API

- `GET /api/v1/bootstrap`
- `GET /api/v1/session`
- `POST /api/v1/session/turns`
- `POST /api/v1/session/continuations/{fingerprint}/responses`
- `GET /api/v1/materials`
- `GET /api/v1/artifacts`
- `POST /api/v1/artifacts/{revision_id}/decisions`
- `GET /api/v1/assessments`
- `POST /api/v1/assessments/{presentation_id}/attempts`
- `POST /api/v1/assessments/{attempt_id}/grade`
- `GET /api/v1/evidence`
- `GET /api/v1/recall/due`
- `POST /api/v1/recall/{revision_id}/reviews`
- `GET /api/v1/context/conflicts`
- `POST /api/v1/context/conflicts/{kind}/resolve`

All routes are JSON, schema-versioned, size-bounded, same-origin, and
`Cache-Control: no-store`. The compatibility routes `/api/state` and
`/api/entry` remain deterministic demo endpoints until removed by a separate
release decision.

## Conversation Commit Gate

The first durable mutation must prove:

1. record the learner turn through `SessionTurnService`;
2. run or resume `TutorHostRunner`;
3. commit a verified assistant/capability outcome through a canonical owner;
4. return a fresh `TutorSnapshotV1`;
5. repeat an identical lost-response request without duplicate events.

`SessionTurnService.record_assistant_turn` currently requires a verified
playbook run while `TutorHostRunner` can return presentation-only messages or
questions. If those outcomes lack a canonical owner, approve one narrow
application contract before exposing the mutation route. The HTTP handler must
never append events directly.

## Passes

1. UI-00: clean main baseline, selected Referto extraction, active plan.
2. UI-01: conversation persistence contract and idempotent end-to-end tracer.
3. UI-02: packaged Referto shell, navigation, responsive states, session bind.
4. UI-03: materials and individual artifact decisions.
5. UI-04: assessments and learner evidence.
6. UI-05: recall queue/reviews and mobile parity.
7. UI-06: context resolution and honest dashboard/unavailable plan.
8. UI-07: public-demo hardening, deployment packaging, docs, full verification.

## Verification

- Focused unit and route-contract tests for every pass.
- Integration tests over a temporary real `LocalRepository`.
- Lost-response retry, stale expected-sequence, restart/reload, cross-course,
  oversized-input, and bounded-serialization tests.
- Browser inspection at desktop and mobile viewports, console check, and
  comparison against the Referto source.
- `python -m pytest`, `python -m ruff check .`, `python -m mypy src tests`.
- Clean wheel install and packaged static-asset check.
- Container health/startup smoke for public-demo mode.

## Current Handoff

- Active pass: UI-01 conversation persistence contract, in parallel with the
  isolated UI-02 asset materialization.
- Baseline: clean branch `codex/cardine-ui-integration` from `main` at
  `e18f670`.
- Next: approve the narrow conversation owner, implement it with tests, then
  integrate the Referto asset pass and bind its first real routes.
- Blocker: public hosting target is not yet selected; production packaging can
  remain target-neutral until deployment.
