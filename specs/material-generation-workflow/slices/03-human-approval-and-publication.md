# Slice 03: human approval and canonical publication

Status: proposed; depends on accepted Slice 02.

## Contract unlocked

The learner can explicitly accept or reject each member of a generated pair.
Only publishable HUMAN-accepted outputs become correctly provenanced canonical
sources and enter the existing indexing path.

## API seam and ownership

- Reuse the existing artifact decision service and expected-sequence conflict
  handling; do not create material-specific decision events.
- Add material publication reconciliation driven by accepted artifact state.
- `GeneratedSourceMaterializer` owns deterministic derived source IDs and the
  `source.revision_ingested@2` event.
- Existing repository indexing coordinator remains the only index writer.

Application seam:

```text
decide_materials(job_id, decisions, expected_sequence, human_context) -> receipt
reconcile_publication(job_id) -> publication view
```

## Partial-approval rule

- Accept complete only: publish complete.
- Reject complete: never publish study from that pair.
- Accept study while complete is pending: persist approval but mark study
  `approved_blocked_on_complete`; publish neither study nor a sibling decision.
- Later accept complete: publish complete, then study, using deterministic IDs.
- Rejection/pending never becomes an indexed source.

## Playable review surface

A headless end-to-end journey generates a pair and exercises:

- both accepted;
- complete accepted / study rejected;
- study accepted before complete;
- complete rejected after study approval;
- crash after decision but before source event;
- crash after source event but before indexing queue acknowledgement.

The report shows artifact decisions, derived source lineage, index status,
retrieval results and a pinned flashcard request over published material.

## Verification

- Only `PrincipalKind.HUMAN` can decide.
- Decision retry and publication retry do not duplicate events, sources or
  index jobs.
- Complete source directly parents the transcript; study directly parents the
  complete blob and retains root transcript lineage.
- A source retired/superseded between proposal and decision blocks publication
  and requests regeneration.
- Replayed repository state reproduces decision/publication status exactly.
- Published materials are retrievable/citable and eligible for existing lesson
  pin and flashcard generation.
- Pending/rejected/blocked material is absent from source catalogs and indexes.
- Existing recall continues to use accepted flashcards only.
- Focused integration/replay tests, Ruff, strict mypy and `git diff --check`.
- Security review of decision authority, event identity, lineage spoofing,
  materialization and publication races.

## Human feedback that changes this slice

- Allowing study publication without accepted complete lineage.
- Requiring all-or-nothing HUMAN decisions instead of independent decisions.
- Requiring editing before acceptance rather than regeneration.

Stop after the headless lifecycle is accepted. Do not add browser triggers to a
publication contract that is still changing.
