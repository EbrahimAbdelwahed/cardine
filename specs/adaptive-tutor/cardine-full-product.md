# Feature Spec: Cardine full repository-backed product

Status: Implemented and validated locally
Owner: Product orchestrator
Date: 2026-07-30

## Grilling Evidence

- Session/artifact:
  - `docs/decisions/ADR-0015--persist-validated-host-presentations.md`
  - `dev/plans/2026-07-29-1315--product-shell--connect-cardine-ui-to-harness--plan.md`
  - authenticated Claude/Cardine UI audit recorded under
    `dev/plans/assets/claude-polish-audit/`
  - repository inventory performed 2026-07-30 against the current worktree
- Decision state: approved by the user for implementation through E2E closure
- ADR/glossary changes:
  - ADR-0015 owns durable host presentations and conversation orchestration.
  - A new ADR is required only if the exam plan introduces canonical writes.
  - No glossary change is required for existing artifact, assessment, recall,
    evidence, source, or study-context owners.

## Goal

Every visible Cardine section and control operates against the real local
Study Agent repository, survives reload/restart, and is validated by an
end-to-end browser flow without moving domain behavior into the browser.

## Problem

Cardine has a polished, complete interaction shell, but repository-backed mode
currently exposes only grounded chat, source reads, a partial evidence view,
and read-only context conflicts. Artifact, assessment, recall, continuation,
conflict-resolution, and plan controls have no live application binding.

## Users

- Primary: the owner studying medicine in a private Cardine installation.
- Secondary: maintainers extending the Study Agent harness without duplicating
  canonical state or provider policy in UI code.

## In Scope

- Durable adaptive conversation through `TutorHostRunner`, including direct
  messages, learner questions, suspension, resume, exact retry, and reload.
- GPT-5.6 Luna-backed natural-language behavior through a host-owned adapter.
- Source-grounded artifact generation, proposal listing, and explicit
  accept/reject decisions.
- Assessment presentation, response, deterministic or verified grading, and
  replayable learner-evidence views.
- FSRS-backed due recall and four-rating review commands.
- Learner-context conflict resolution.
- Real bootstrap counts and Today aggregation.
- Real source catalog and bounded provenance metadata.
- A truthful exam-plan view derived from explicit canonical inputs. Canonical
  plan writes require an approved ADR and dedicated owner.
- Same-origin, schema-versioned, idempotent, sequence-fenced commands.
- Desktop/mobile UI states and all-flow E2E verification.
- Optional private single-owner product-shell access, runtime-only Luna
  credential entry, and account/settings metadata under ADR-0019.

## Out of Scope

- Public or multi-user repository mutation.
- Hosting provider selection, remote deployment, multi-user authentication, or
  tenancy. ADR-0019 permits only the optional single-owner `study_agent.demo`
  access boundary.
- Browser access to SQLite, filesystem paths, provider credentials, or
  capability authority.
- Invented mastery, retention, study-time, coverage, or exam-readiness values.
- Automatic acceptance of generated artifacts.
- Anki as the canonical scheduler.
- Provider-specific model calls from UI components.

## User Stories

- As a learner, I can converse with an adaptive tutor and resume a clarification
  after reload.
- As a learner, I can generate grounded study artifacts and explicitly accept
  or reject each proposal.
- As a learner, I can answer an accepted assessment item and see its canonical
  grade and evidence.
- As a learner, I can reveal a due flashcard and record Again, Hard, Good, or
  Easy without the browser calculating the next schedule.
- As a learner, I can resolve contradictory study-context values explicitly.
- As a learner, I can inspect the canonical sources and their provenance.
- As a learner, I can see a truthful plan assembled from my explicit deadline,
  available study budget, due recall, pending proposals, and assessment work.

## Domain Model

Affected entities:

- `TutorPresentationRecord`: canonical direct tutor text/question/continuation.
- `TutorContinuationRecord`: durable opaque operational continuation.
- `ArtifactRevisionRecord`: proposed and decided study artifact.
- `PresentationRecord`, `AttemptRecord`, `GradeRecord`: assessment lifecycle.
- `ReviewRecord`, `AppliedSchedule`: recall lifecycle.
- `LearnerEvidenceSnapshot`: assessment-derived evidence, never generic mastery.
- `StudyContextSnapshot`: explicit learner statements and scalar conflicts.
- `SourceRevisionRecord`: owner-ingested, immutable source revision.
- `StudyPlanSnapshot`: projection-only composition unless a plan-write ADR is
  separately approved.

## API / Interface Contract

All commands retain the existing envelope:

```json
{
  "schema_version": 1,
  "request_id": "opaque",
  "expected_sequence": 42,
  "payload": {}
}
```

Routes:

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
- `GET /api/v1/plan`

The transport validates only HTTP concerns and delegates to one
transport-independent application boundary. Server composition owns
repository/course/session selection, principal kind, capabilities,
idempotency domains, and provider environment.

## Prompt Behavior

- Prompt IDs affected: none for the baseline Luna migration; the existing
  closed tutor-decision prompt and schema are preserved.
- Course profile inputs: canonical course profile and study-context projection.
- Output schema: existing closed `TutorDecision` union.
- Grounding requirements: explanations and generated study artifacts must cite
  canonical source revisions; insufficient evidence remains a valid outcome.
- Eval fixtures required: direct answer, clarification, capability selection,
  invalid decision, insufficient evidence, and provider retry.

## RAG / Source Grounding

- Required sources: canonical active source revisions for the selected course.
- Citation behavior: revision/chunk locators and checksums remain server-owned;
  UI receives bounded provenance DTOs.
- Unsupported-answer behavior: no answer or artifact is fabricated; the tutor
  returns the canonical insufficient-evidence state.

## UX Notes

- Loading: disable the initiating control, retain local selection, announce
  working status, and prevent duplicate submission.
- Empty: distinguish a valid empty queue from an unavailable owner.
- Error: preserve canonical state, present retry only for the exact request,
  and reload on sequence conflicts.
- Accessibility: keyboard-only operation, visible focus, status/live regions,
  bounded dialogs/drawers, mobile parity, and reduced-motion compatibility.

## Risks

- Capability completion could be incorrectly converted into arbitrary tutor
  text; only closed presentation receipts may enter conversation history.
- Artifact generation has more durable runtime dependencies than artifact
  decision/listing and must not be faked by seeding browser state.
- Recall requires an explicitly configured FSRS adapter and accepted/enrolled
  flashcard revisions.
- Plan data can easily become invented analytics; projection fields must name
  their canonical inputs and unavailable values.
- Provider calls must not happen for stale commands.
- Cross-route course sequence races require exact retry before stale rejection
  and CAS-bound commits.

## Acceptance Criteria

- [x] FP-01 Adaptive chat direct messages, questions, suspension, response,
  reload, and exact retry are canonical.
- [x] FP-02 GPT-5.6 Luna drives the closed tutor-decision contract with secrets
  confined to environment-owned adapters.
- [x] FP-03 A grounded artifact can be generated, listed, accepted/rejected,
  and restored after restart.
- [x] FP-04 An accepted assessment can be presented, attempted, graded, and
  reflected in learner evidence.
- [x] FP-05 An accepted flashcard can be enrolled, become due, be reviewed with
  four ratings, and receive a canonical next schedule.
- [x] FP-06 A learner-context conflict can be resolved explicitly and remains
  resolved after reload.
- [x] FP-07 Today counts and feature availability come from real projections.
- [x] FP-08 Course sources and provenance can be inspected without exposing
  server filesystem authority to the browser.
- [x] FP-09 Plan displays only explicitly supported canonical inputs and never
  fabricates mastery, retention, or coverage.
- [x] FP-10 Every visible route/control has loading, empty, success, conflict,
  error, restart, and keyboard/mobile E2E coverage where applicable.
- [x] FP-11 Public demo remains stateless, sanitized, and incapable of
  repository mutation.

## Verification

- Unit: application DTOs, command validation, idempotency, sequence fencing,
  codecs, projections, and UI asset contracts.
- Integration: temporary real `LocalRepository` for every vertical lifecycle,
  including restart and external-writer races.
- Evals: GPT-5.6 Luna tutor decisions and grounded generated artifacts.
- Manual: in-app browser desktop/mobile traversal, submit/reload, provenance,
  console, focus, and reduced-motion checks.
- Full gates: pytest, Ruff, mypy when available, wheel contents, clean install,
  and public-demo isolation.

## Open Questions

- None block the first bead. Exam-plan write authority is intentionally deferred
  to its bead's ADR decision.

## Decision Log

- Existing domain owners are reused; the UI application never appends events.
- GPT-5.6 Luna is the base natural-language model for this product composition.
- Hosting remains out of scope until the complete localhost product is green.
- Plan begins as a projection-only view. Any mutation requires an ADR.
- The existing dependency-free browser remains the product surface; no frontend
  framework or new dependency is introduced.

## Task Beads

- `TUT-08A`: durable adaptive conversation application.
- `TUT-08B`: DeepSeek tutor-host repository composition.
- `TUT-08C`: reference runtime, materials, proposals, and context commands.
- `TUT-08D`: assessments and learner evidence.
- `TUT-08E`: FSRS recall flow.
- `TUT-08F`: context/Today aggregation and truthful exam readiness.
- `TUT-08G`: all-flow E2E and release closure.
- `TUT-08H`: GPT-5.6 Luna base-adapter migration.
