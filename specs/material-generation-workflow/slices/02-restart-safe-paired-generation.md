# Slice 02: restart-safe paired generation

Status: implemented and verified; awaiting user review before Slice 03.

## Contract unlocked

One exact current transcript revision can produce one atomic proposal batch
containing a complete material and its derived study material, through the
configured GPT-5.6 Luna adapter, with durable stage recovery and no duplicate
provider work after a completed checkpoint.

## API seam and ownership

Introduce a cohesive `src/cardine/materials/` module owning:

- `PinnedTranscriptInput` and strict request/view codecs;
- `MaterialGenerationState` and CAS checkpoint codec;
- `MaterialGenerationService.request_pair(...)`, `reconcile(...)`, `get(...)`
  and `list(...)`;
- deterministic unit splitting and validated structured segment boundaries;
- versioned prompts `complete-segment@1`, `complete-merge@1` and
  `study-from-complete@1`, plus the structured boundary prompt
  `material-boundaries@1`;
- a workflow-specific coordinator that reuses the existing model port, blob
  store, run store and generated-artifact service.

Do not create a second model client or generic job platform. If the existing
generation-worker primitive can own an individual isolated stage without
weakening its contract, extend it with a material task kind; the parent material
coordinator still owns paired stage order.

Public application seam for this slice:

```text
request_pair(course_id, session_id, exact_source_pin, request_id) -> job view
reconcile(job_id, bounded_budget) -> job view
get(job_id) -> job view
```

`list(...)` is deferred to the product-surface slice. The existing durable run
store is intentionally key-addressed and its namespaced adapter hashes keys;
adding truthful enumeration here would require a second index/table or a wider
generic store contract.

## Pipeline and failure behavior

- Validate an exact full-source, current, active `.txt`/`.md` pin before
  constructing the provider adapter.
- Use only the configured `openai-gpt-5.6-luna` model and server-owned
  `OPENAI_API_KEY` environment reference, behind provider consent.
- Persist each successful segment and stage output in the blob store before
  advancing CAS state.
- Fail retryably on bounded provider timeout/unavailability; fail terminally on
  malformed output, coverage gaps, pin mismatch or unsupported bounds.
- If the input becomes retired or superseded before proposals are committed,
  mark the job stale and publish no proposal batch.
- Record both lesson-material proposals atomically only after all validators
  pass. A one-output proposal batch is invalid.

## Playable review surface

A scripted-model CLI/headless probe runs a multi-segment lesson, prints the
stage trace and opens both immutable output blobs for inspection. A second probe
restarts after every stage and proves it resumes at the next incomplete stage.

## Verification

- Complete output consumes exact transcript bytes; study consumes exact
  complete blob and not the transcript directly.
- Strict boundary JSON has ordered, bounded, gap-free coverage.
- Mechanically check uncertainty markers, teacher-emphasis markers and numeric
  anchors where present; document limitations instead of claiming semantic
  proof.
- Prompt/model/pipeline pin changes conflict with an existing job identity.
- Same request retry returns the same job/batch.
- Restart at every state creates no duplicate successful provider stage,
  proposal event or blob identity.
- Missing consent/API configuration, foreign/stale/partial pin and unsupported
  source kind perform zero provider calls.
- Partial output, oversized Markdown and validator failure create no proposals.
- Existing flashcard worker recovery remains green.
- Focused pytest, Ruff, strict mypy, package-data/wheel check and
  `git diff --check`.

## Human feedback that changes this slice

- Desired structure or fidelity rules for either output.
- Maximum supported transcript/material size.
- Whether superseded-input jobs should be reviewable but permanently
  unpublishable instead of becoming stale before proposal creation.

Stop after inspecting the scripted outputs and recovery report.

## Implementation evidence

- `MaterialGenerationService` owns a bounded CAS state with durable blob
  references, stage claims, strict Luna receipts and restart from the next
  incomplete checkpoint.
- `LocalRepository.material_transcript_pin(...)` resolves only the exact
  current, active, non-retired original/extracted text or Markdown revision.
  `LocalRepository.material_generation(...)` checks that pin, active session,
  provider consent and the exact Luna/`OPENAI_API_KEY` configuration before it
  constructs the adapter, then repeats canonical preflight at every stage.
- Complete-segment, merge and study calls use strict
  `{markdown, limitations}` JSON; study receives the complete blob as its only
  content parent.
- The verified-batch adapter requires canonical root chunk commitments,
  reconstructs the prompt/model/usage receipt chain and exposes exactly the
  complete/study pair to the existing `ArtifactService`.
- The repository integration journey commits one proposal-batch event with two
  revisions, reopens the repository and reaches the same terminal job without
  another provider call or proposal event.
- A fresh-service two-segment journey resumes after every completed provider
  checkpoint without repeating a call. First-version limits cap transcripts at
  512,000 characters, units at 256, segments at 16, each model request at 1.5M
  characters and a job at 24 provider attempts.
- Enumeration remains deferred: the current namespaced run store is
  key-addressed, so a truthful `list(...)` needs a separately approved durable
  index/store contract.
