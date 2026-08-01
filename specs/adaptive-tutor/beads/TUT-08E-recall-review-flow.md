# Task Bead: TUT-08E recall queue and review flow

Status: Complete
Priority: P0
Type: product tracer-bullet
Depends On: TUT-08C

## Outcome

Cardine shows due accepted flashcards, reveals their answers locally, records
all four recall ratings through `RecallService`, and restores the canonical
next schedule after restart.

## Slice Strategy

tracer-bullet

Fresh Context Fit: yes

## Spec Coverage

- FP-05 complete recall lifecycle.
- FP-07 due-review counts.
- FP-10 recall states/E2E.

## Grilling Evidence

- Session/artifact:
  - `specs/adaptive-tutor/cardine-full-product.md`
  - `specs/adaptive-tutor/beads/TUT-07-recall-and-scheduling.md`
  - `docs/decisions/ADR-0016--closed-capability-completion-handoff.md`
- Decision state: approved
- ADR/glossary changes: ADR-0016 recovery semantics; none additional

## Worker Profile

reuse `cardine-product-slice`

Rationale:

The recall ledger, due view, and optional FSRS adapter exist; this is a bounded
product composition and UI tracer.

## Context

Recall reads are composed but Cardine always reports unavailable. Commands
exist only when an explicit scheduler is installed/configured. Accepted
flashcards may require a separate recoverable enrollment step before appearing
due.

## What To Do

- Compose an explicitly injected optional FSRS scheduler without adding it to
  base imports.
- Keep absence/unavailability honest and isolated from chat.
- Implement deterministic accepted-flashcard enrollment recovery according to
  ADR-0016.
- Join due rows with accepted flashcard content and bounded provenance.
- Keep reveal browser-local; only rating is a canonical command.
- Bind Again/Hard/Good/Easy to `RecallService.review`.
- Filter superseded/non-current/non-accepted revisions.
- Return canonical next schedule metadata and refresh counts.

## Likely Files / Packages

- reference product/repository composition
- optional scheduling adapter wiring
- `src/study_agent/demo/ui_application.py`
- `src/study_agent/demo/browser.js`
- recall integration and browser E2E tests

## Acceptance Criteria

- [x] Accepted current flashcards enroll exactly once and recover after a failed
  post-acceptance enrollment step.
- [x] Due queue ordering and content come from canonical views.
- [x] All four ratings commit one review plus one schedule atomically.
- [x] Exact retry returns the prior review/schedule.
- [x] Superseded or unaccepted revisions cannot appear or accept reviews.
- [x] Missing optional scheduler is unavailable, not globally degraded.
- [x] Restart reconstructs identical due/review state.

## Verification

- TUT-07 unit/contract/real-FSRS integration lanes
- product accept/enroll/due/four-rating/restart tests
- browser reveal/rating/stale/retry E2E
- base import without FSRS
- Ruff, mypy when available, and `git diff --check`

## Out Of Scope

- Anki scheduling or browser-computed dates.
- Generic retention/mastery scores.

## Notes / Handoff

- Artifact acceptance remains committed if enrollment later fails.
