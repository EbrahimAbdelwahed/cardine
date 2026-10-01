# ADR-0021: Bounded semantic judgement and shared derived document indexing

Status: accepted for implementation; production activation gated by evaluation.

## Context

The Jev/PageIndex unified specification targets reusable Harness boundaries.
Cardine carries its own copied core and owns its product tutor composition. Its
current main branch has canonical frozen substrates and deterministic lesson
planning, but the KB v0.2 tree/unitizer implementation is still absent. This
change introduces the available seams without manufacturing those missing
canonical owners or treating another checkout as a dependency.

## Decision

Choice judgements are derived, non-authoritative answers to finite closed
questions. `ChoiceJudgementPort` is shared by flashcard selection and tutor
routing; one optional Jev adapter owns transport only. Consumers validate the
complete probability distribution and own calibrated threshold policies.

`DocumentIndex` is the intended primary rebuildable document structure. Its
nodes, summaries and provider identifiers carry no canonical authority. The
index commits to source, revision, substrate, producer and configuration.
Reconciliation verifies frozen bytes and resolves navigation locators to
host-owned spans. The existing qualified PageIndex subprocess is reused at the
Cardine adapter boundary rather than adding a second semantic parser.

Flashcard semantic filtering happens before deterministic planning. The receipt
retains all analyzed candidates; only CORE and SUPPORTING produce paragraph
spans. Weak judgements, provider failure and incomplete excerpts retain content.
The selected lesson scope cannot expand to unrelated indexed passages. Planner,
worker and trusted scope contracts are unchanged.

Jev selects tutor routes and bounded response/capability choices. The model then
receives only the selected route's payload schema when generation is needed.
The router returns existing decisions, validated by the existing validator;
`TutorHostRunner` retains lifecycle, freshness and execution authority. Legacy
routing remains authoritative in SHADOW and available as an explicit emergency
fallback. It is not a second normal semantic router in ON mode.

No final UnitId owner is introduced. When KB unitization exists, structural
index drafts must flow through its single materialization owner, preserving
historical resolution and replay. Legacy structural drafting is migration code
with removal gated on grounding, retrieval, flashcard quality and replay.

## Consequences

Core installation remains independent of provider SDKs. Provider credentials
remain environment references and derived receipts never append canonical
study events. OFF preserves current behavior; SHADOW compares candidate output;
ON requires labeled-data recall/routing quality and measured cost/latency gates.
Configuration schema migration, production activation and primary-path removal
remain blocked until those gates and missing KB contracts are satisfied.

See [the implementation ledger](../../specs/jev-pageindex/README.md) for current
verification and pickup. ADR-0010 still owns the unchanged worker design.
