# ADR-0021: Bounded semantic judgement and shared derived document indexing

Status: accepted; implemented in Cardine.

## Context

The unified specification assumes Harness KB v0.2 unitization. Cardine instead
owns immutable SourceChunks, a qualified PageIndex worker and product tutor
composition. Replacing canonical chunk history to imitate absent KB objects would
break citations and generated-artifact recovery. The owner also explicitly
selected OpenRouter API and excluded benchmark work on 2026-10-01.

## Decision

Choice is a provider-neutral finite judgement. One optional async OpenRouter
Decisions adapter owns authentication, concurrency, deadlines and transport
retries; consumer policies own thresholds. Resolved model versions are explicit
configuration so aliases cannot silently reuse versioned semantic analysis.

The PageIndex coordinator persists one primary, derived DocumentIndex. Its
navigation projection and flashcard anchors address frozen normalized bytes.
PDF input uses admitted Markdown plus verified original PDF provenance and an
exact page map; indexing never introduces another extraction authority. ON
fails explicitly when indexing is unavailable. Legacy navigation remains only
in OFF/SHADOW compatibility paths.

Identity-free structural drafts map to existing canonical chunks and citations
in knowledge/unitizer. ingestion.identity remains the sole ChunkId creation
owner. Flashcard runtime classification uses whole chunks before filtering;
merging classified subspans afterward would reintroduce excluded content and
violate unchanged full-chunk evidence commitments. Planner, worker, human
proposal decisions and canonical event history remain authoritative.

Tutor routing selects legal routes, advertised capabilities/tools and closed
answers. Any generated payload sees only its selected schema, then existing
validation and execution authority apply. Full-tutor routing is confined to
OFF/SHADOW and explicitly configured emergency fallback.

Derived indexes and analysis caches are versioned and rebuildable; they cannot
write canonical events. Cached analysis validates canonical candidates,
judgement provenance and consumer policy. Outages preserve flashcard content
without permanently caching provider failure. Provider consent applies to each
Jev call using the same course boundary as generative models.

## Consequences

Strict repository configuration v2 contains credential environment references,
provider operations and immutable consumer policy. V1 readers explicitly migrate
to OFF without rewriting existing files. The owner waived benchmarks; offline
contract, migration, replay, packaging and regression tests remain required.
Conservative configurable thresholds are not claimed to be calibrated quality
results. No live calls, deployment or merge follow from this implementation.

See [the implementation record](../../specs/jev-pageindex/README.md) and
ADR-0010 for the unchanged worker design.
