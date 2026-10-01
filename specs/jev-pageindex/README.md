# Bounded judgement and shared document indexing

The owner requested application of [the unified specification](unified-spec.md)
to Cardine. On 2026-10-01 the owner explicitly excluded benchmarks and selected
OpenRouter API for Jev. These instructions supersede the original SDK and
benchmark rollout requirements. No quality, recall, cost or latency measurements
are claimed, and no live provider request is part of verification.

## Current pickup

Implementation and production composition are complete on
`codex/jev-pageindex-refactor`, continuing draft [PR #10](https://github.com/EbrahimAbdelwahed/cardine/pull/10).
Final full offline verification, package verification and publication are the
remaining closeout steps. No merge, deployment or study-store migration is authorized.

## Architecture decisions

Cardine/main has immutable SourceChunks and historical citations, rather than
Harness KB v0.2 DocumentTree/RetrievableUnit/UnitId. The unitizer requirement is
adapted to that actual authority: identity-free index drafts reuse existing
canonical ChunkIds through `knowledge/unitizer.py`; `ingestion.identity` remains
the sole creation authority. The runtime classifies whole canonical chunks
before filtering, preventing a later merge from reintroducing excluded slices.
No canonical event, chunk identity or planner/worker contract changes.

The existing PageIndex coordinator now owns one persisted `DocumentIndex`;
lesson navigation and flashcard anchors consume it. Schema1 derived caches
rebuild into schema2; disabled state survives configuration changes. ON has no
hidden legacy semantic parser fallback. PDF indexing uses the already admitted
normalized Markdown and verified original bytes/page map, with explicit
provenance; it does not run a second PDF extractor. Historical PDF sources without
an exact page map report a failed derived index instead of approximating pages.

OpenRouter's [Decisions REST protocol](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request)
is separate from chat completions. Optional `.[jev]` installs native async HTTP;
there is no TypeSafe SDK dependency. Server-owned `OPENROUTER_API_KEY` is an
environment reference. The resolved model version is explicitly pinned separately
from the request alias so changed provider versions cannot silently share analysis.

`FlashcardSemanticPreprocessor` runs before the unchanged deterministic planner.
Only CORE/SUPPORTING whole chunks reach the worker; canonical offsets/locators
are reconciled again by the existing resolver. OFF/SHADOW retain the exact
original lesson unit. Persisted analysis includes bounded metadata and safe
receipts, never source excerpts; provider failures retain content without poisoning
the cache. Input/index/policy/model changes invalidate analysis.

`RoutingTutorDecisionPort` covers all existing decisions, including INVOKE_TOOL.
Jev selects legal routes and advertised actions, and generation receives only the
chosen payload schema. Existing validators and runner remain authoritative.
Full routing is confined to OFF, SHADOW and explicit emergency fallback; ON
success has no second semantic router. Each Jev call uses existing course consent.

## Configuration

Configuration v2 separates `model`, `judgement`, `document_index`, and `features`.
Reading v1 explicitly upgrades to v2 with features OFF; it does not modify the
file. Existing installations retain their behavior until flags are changed.
Consumer thresholds are explicit conservative policy values, not calibrated claims.
ON requires a configured judgement adapter; ON flashcard semantics requires ON
indexing. Operational concurrency is separate from semantic fingerprints.

See `study_agent.repository_config` for the strict schema, and the activation
example added during closeout. Canonical data and local credentials are not
included in this PR. Live provider evaluation is excluded by the owner.

## Verification

Focused provider, index/PDF, unitizer/replay, routing/runner and real repository
pipeline tests pass. Integrated full run: 2558 passed, four optional skips; two
schema-version expectations are updated, and the committed ownership audit is
being refreshed before the final rerun. Ruff and strict mypy pass on 606 files.
The original 322-row migration ledger remains frozen; evolution has exact hashes.

See [ADR-0021](../../docs/decisions/ADR-0021--bounded-judgement-and-derived-document-index.md).
