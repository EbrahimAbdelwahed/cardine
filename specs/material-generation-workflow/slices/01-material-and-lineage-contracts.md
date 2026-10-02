# Slice 01: material and lineage contracts

Status: implemented; awaiting user review.

## Contract unlocked

Cardine can represent a long complete/study Markdown proposal without embedding
the document in event payloads, and can represent an approved generated source
without claiming that it is original or mechanically extracted.

No model call, browser route or production material publication is introduced
in this slice.

## API seam and ownership

- Add `LESSON_MATERIAL` and `LessonMaterialContent` to the existing artifact
  domain/content codecs.
- Store generated Markdown in the existing blob store; artifact content owns
  only exact blob identity, variant, length, parent digest and limitations.
- Add `GeneratedDocumentProvenance` and `GeneratedSourceMaterializer` as the
  sole generated-source admission path.
- Add strict generated `source.revision_ingested@2` encoding/decoding and keep
  v1 replay byte-stable.
- Factor any shared normalization/chunk preparation out of
  `TextIngestionService` so original ingestion and generated materialization
  consume one primitive. Do not duplicate chunking or blob validation.

Likely files:

- `src/study_agent/domain/artifact.py`
- `src/study_agent/domain/provenance.py`
- `src/study_agent/domain/source.py`
- `src/study_agent/artifacts/content.py`
- `src/study_agent/ingestion/events.py`
- `src/study_agent/ingestion/projection.py`
- `src/study_agent/ingestion/service.py`
- new Cardine materializer module under `src/cardine/materials/`
- focused unit/contract/replay tests

## Playable review surface

A headless fixture creates:

1. a complete lesson-material envelope;
2. a study envelope whose parent digest is the complete blob;
3. an approved generated-source manifest for each;
4. a round-trip/replay report showing v1 sources unchanged and v2 lineage
   preserved.

No event is appended by this fixture unless it passes every validation.

## Verification

- Exact codec round trips and unknown-field rejection.
- Blob/hash/length mismatch rejection.
- Study direct-parent mismatch rejection.
- Generated source rejects missing HUMAN decision, missing artifact revision,
  wrong origin or wrong structure origin.
- Original/extracted source rejects generated provenance.
- Existing v1 source fixtures remain byte-stable and replay unchanged.
- v2 generated source replays to the same canonical chunks and lineage.
- Generated Markdown never appears inline in the source/artifact event payload.
- Focused Ruff, strict mypy, architecture boundaries and `git diff --check`.
- Security review of untrusted Markdown, blob integrity, event decoding and
  privilege boundary.

## Must stay green

- Existing source ingestion/content resolution and PDF admission tests.
- Existing artifact flashcard, assessment, exam and study-brief codecs.
- Export/replay and source integrity tests.

## Human feedback that changes this slice

- Rejecting the new `LESSON_MATERIAL` kind.
- Requiring generated material to remain non-canonical forever.
- Choosing a different partial-approval dependency rule.

Stop after this slice's review. Do not start provider-backed generation until
the lineage contract is accepted.

## Implementation evidence

- Added the blob-backed `LESSON_MATERIAL` complete/study artifact contract and
  exact generated-document provenance codecs.
- Added strict `source.revision_ingested@2` identity, payload and blob
  validation while preserving the v1 contract.
- Added a SERVICE-only generated-source materializer with mandatory canonical
  projection preflight, deterministic idempotency and complete-before-study
  admission.
- Added stateful replay validation for the exact current root, current accepted
  artifact, HUMAN decision and paired lineage across projection, export and
  retrieval paths.
- Generated chunk manifests are opaque; Markdown bodies and headings remain in
  the content-addressed blob store.
- Focused verification: 73 tests passed; Ruff, strict targeted mypy,
  `compileall`, security re-review and `git diff --check` passed.
- Full-suite observation: 2,367 passed, 31 skipped and 27 unrelated failures
  from the pre-existing dirty worktree and sandboxed socket/PDF-worker gates.
