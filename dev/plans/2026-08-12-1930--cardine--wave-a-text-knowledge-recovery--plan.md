# Plan: Wave A text knowledge recovery

Date: 2026-08-12 19:30
Area: cardine-knowledge

## Goal

Recover the reviewed text/Markdown study vertical after A2: derived PageIndex navigation, explicit lesson selection, pin-scoped grounded answers, reviewable pinned flashcards, and accepted-only recall across restart.

## Scope

- In scope: Cardine-owned immutable navigation DTOs; hash-bound isolated structural Markdown worker; per-revision CAS projection/status/backfill; bounded lexical fallback; course-scoped lesson search and explicit ambiguity selection; complete stale-safe source pins; canonical pinned grounding; pinned flashcard generation; browser/CLI journey; individual and approved atomic HUMAN decisions; recall regression.
- Out of scope: AnyDoc/PDF runtime enablement; OCR; changes to Harness event/source/citation truth; PageIndex-authored evidence; provider auto-consent; implicit selection; dual event or retrieval backends; CA-04 package pivot.

## Approach

1. Freeze A2 consent and source-lifetime behavior with replay and provider-zero gates.
2. Add pure Cardine lesson/source-pin DTOs and deterministic Markdown section selection over canonical source text; use existing SQLite FTS only for lexical evidence.
3. Qualify and package only the exact hash-bound PageIndex structural function subset as non-importable evidence; execute in an isolated bounded worker and map every candidate back to canonical revision offsets or discard it.
4. Persist one derived per-revision projection in the existing namespaced run store with queued/indexing/ready/degraded/failed/disabled states, bounded leases/retries, restart reconciliation, and truthful lexical fallback.
5. Add one Cardine application service shared by CLI/browser for search, ambiguity, complete pin selection, and pinned grounded ask. Validate course/source/revision/span/digest/catalog before provider construction.
6. Scope flashcard proposal composition to the same complete pin, expose bounded reviewable front/back content, integrate individual plus Harness atomic HUMAN batch decisions, and retain accepted-current-only recall.
7. Prove one aggregate Markdown journey (`Lezione 1` and `Lezione 2`) through restart, retirement, PageIndex disabled/failure fallback, grounded citation, proposal review, decision, enrollment, and review.

## Risks

- PageIndex is navigation only; any unmappable or ambiguous structural node must be discarded rather than becoming evidence.
- Search-triggered indexing must be bounded and cannot block on every course source. Admission/startup reconciliation uses a fixed work budget.
- Source retirement changes the active derived view only. Historical blobs, events, citations, cards, decisions, and reviews remain resolvable.
- A complete pin containing no whole canonical chunks must fail before provider invocation.
- The recovered Harness atomic-batch commit must be consumed through a narrow adapter; Cardine must not emulate atomicity with repeated single decisions.

## Verification

- Focused unit tests for section scanning, mapping, worker security, CAS lifecycle, stale/foreign/partial pins, and provider-zero failures.
- Existing text ingestion, FTS retrieval, source replay/content resolution, grounding ask, flashcard proposal, artifact lifecycle, recall, CLI, and browser journeys.
- Ruff, mypy, JavaScript syntax, `git diff --check`, ownership audit, wheel/sdist verifier, and relevant full pytest classification.
