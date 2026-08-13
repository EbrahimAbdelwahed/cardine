# Plan: Cardine Wave A studyable product

Date: 2026-08-11 23:45 CEST
Area: cardine

## Goal

Ship a private single-user Cardine journey that admits text, Markdown, and
text-bearing PDF sources; indexes and searches aggregate course material with a
derived PageIndex projection; produces canonically grounded chat answers; creates
reviewable flashcard proposals; and enrolls only HUMAN-accepted cards in recall.

The studyable checkpoint may precede terminal package adoption, but Wave A is
not terminally complete until the existing adoption/release gates through CA-10
are also closed without product reimplementation.

## Product Contract

- Upload and backfill are automatic; indexing is visible as queued, indexing,
  ready, degraded, or failed.
- Browser search defaults to the course and permits explicit source/section pins.
- Materially ambiguous candidates require learner selection before a provider call.
- PageIndex navigates; only canonical source text supports claims, citations, or flashcards.
- Flashcard generation is available from chat and the selected lesson view.
- Every generated revision remains `PROPOSED`; only HUMAN decisions can accept or reject.
- Only accepted and enrolled flashcards enter recall.
- First-course consent is explicit before Luna sees source text; revocation blocks
  future provider calls without destroying canonical history.

## Architecture Invariants

- Harness remains the canonical owner of source identity/revisions/blobs,
  citations, generic artifact decisions, and recall mechanics.
- Cardine owns product semantics, consent, source lifetime language, derived
  PageIndex state, course selection, composition, and presentation.
- PDF conversion and canonical admission are atomic. Later structural-index
  failure preserves the source and degrades to lexical retrieval.
- PageIndex candidates must map to canonical revision spans or be discarded.
- UI and CLI cross the same Cardine application interfaces.
- Native/package promotions are manual and require new qualification evidence.

## Scope Boundary

Wave A includes provider consent, source lifetime, text/Markdown and qualified
text-bearing PDF admission, visible derived indexing, lesson selection and pins,
grounded conversation, flashcard proposal decisions, accepted-only recall, and
browser/CLI parity.

OCR, image understanding, PageIndex cloud services, vector retrieval,
cross-course search, public tenancy, billing, distributed job infrastructure,
automatic web admission, ambiguous auto-selection, and automatic flashcard
acceptance/enrollment are outside Wave A.

## Release Checkpoints

1. Freeze the final Cardine-facing runtime seam.
2. Establish consent and source-lifetime semantics.
3. Admit canonical text, Markdown, and qualified text-bearing PDFs.
4. Build restart-safe PageIndex projection with lexical fallback.
5. Add course-scoped search, structural pins, and ambiguity handling.
6. Produce grounded conversation from canonical evidence.
7. Complete proposal, HUMAN decision, enrollment, and recall lifecycle.
8. Prove the browser/CLI studyable journey against one repository.
9. Finish terminal Harness adoption and delete temporary transition paths.

## Risks

- PageIndex and PDF page structure can diverge; unresolved mappings fail closed.
- Background indexing must not block source admission or become a generic jobs framework.
- Bulk artifact decisions require one atomic, CAS-bound command.
- Source retirement preserves referenced history; it is not physical erasure.
- Product work must not create a second source, retrieval, event, artifact, or recall pipeline.

## Definition of Done

The studyable checkpoint requires one restart-safe browser and CLI journey that
imports aggregate material, selects a lesson, returns resolvable canonical
evidence, generates and reviews flashcards, proves accepted-only recall, and
retains historical evidence across source revision/retirement and structural
fallback.

Terminal Wave A additionally requires the remaining Harness adoption gates and
removal of temporary transition/oracle paths without changing product semantics.
