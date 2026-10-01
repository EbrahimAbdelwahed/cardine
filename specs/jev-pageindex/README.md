# Bounded judgement and shared document indexing

The owner requested application of [the unified specification](unified-spec.md)
to Cardine. This record covers this repository; Harness has not changed.

## Current pickup

The available contracts and components are implemented. Finish the dependencies
and evaluate labeled shadow data before production wiring or any primary-path
switch. This is a draft implementation, not completed rollout.

Cardine/main at `1163005` has sources/substrates and lesson planning/workers,
but no `DocumentTree`, `RetrievableUnit`, `UnitId` or `knowledge/unitizer.py`.
KB-02 through KB-06 remain unimplemented. The unitizer migration cannot preserve
an owner which does not exist here; do not invent a parallel authority or import
another checkout. Tutor contracts remain in `cardine.hosts`.

## Delivery ledger

- [x] Immutable Choice contract with full finite distribution validation.
- [x] Substrate-bound DocumentIndex; exact pages, lines and Unicode reconciliation.
- [x] Disjoint local candidates covering the source, with explicit binding failures.
- [x] Optional Jev SDK adapter, shared loop budget and bounded transport retries.
- [x] DocumentIndex adapter reusing the qualified PageIndex worker, for Markdown/text.
- [x] Conservative semantic receipt, identity/cache key and CORE/SUPPORTING projection.
- [x] Lesson-scope confinement, complete-excerpt exclusion and unchanged planner/worker.
- [x] Tutor router with closed choices, narrow payloads, validation, fallback/shadow.
- [x] Architecture and evidence gate tests; provider-free wheel checks and Jev CI lane.
- [ ] Labeled-data calibration and live recall/routing/cost/latency benchmarks.
- [ ] Production wiring and versioned provider/feature configuration, after gates.
- [ ] Raw PDF PageIndex indexing/provider decision beyond the qualified Markdown subset.
- [ ] Tutor INVOKE_TOOL accommodation before replacing Cardine's current tutor.
- [ ] KB unitization dependency, one-owner draft/materialization migration and replay.
- [ ] Legacy primary-runtime removal after grounding/retrieval/flashcard/replay gates.

## Contracts and use

Core imports work without Jev dependencies. Install `.[jev]` only when composing
`JevChoiceAdapter`; the credential comes from `TYPESAFE_API_KEY` or server-owned
constructor input, never persisted source configuration. SDK 0.7.2 protocol
fixtures use local MockTransport, not the live API. `JEV_CONCURRENCY` defaults to
64; all adapter instances on one loop share the configured budget. Conflicting
limits fail explicitly. Compare 16/32/64 in separate benchmark runs.

`PageIndexDocumentIndexAdapter` uses the existing qualified, killable PageIndex
worker. It produces derived line locators over frozen normalized Markdown/text;
no extra SDK is required for that bundled subset. Raw PDF requests fail explicitly.
PDF page-range reconciliation is independently supported when a trusted page map
exists. Neither summaries nor provider text becomes evidence.

`FlashcardSemanticAnalyzer` takes an explicit `SemanticPolicy` and a pinned
`judgement_identity` of `producer_id@producer_version/model_id`. A changed or
unexpected producer/model cannot reuse the semantic cache identity. Resolve
canonical text from `DocumentIndexContext`; no source text is retained in the
analysis receipt. `preprocess_generation` returns the exact original unit in
OFF/SHADOW and only its scoped eligible projection in ON. Its provided original
paragraphs bound the lesson; it cannot expand selection to the full-source index.
Projection trims only surrounding whitespace by adjusting canonical offsets to
meet the existing evidence contract. Gaps remain absent from planned slots.

`RoutingTutorDecisionPort` takes `TutorRoutingPolicy`, `ChoiceJudgementPort`,
`ModelPort`, an optional legacy port and an optional derived receipt callback.
Thresholds are required evaluation inputs. SHADOW keeps the legacy decision;
ON uses narrow generation after route choice and explicit emergency fallback.
Pending host validation permits only ANSWER_DIALOGUE, so closed answers need no
route model call. Fixed capability inputs use `{}` or singleton enum values;
the existing strict schema excludes `default`/`const`. No arbitrary map from
trusted context to capability fields is introduced.

No operational cache store, canonical events or repository configuration schema
change was introduced. Components work without a cache; future storage keys
include canonical content, index/configuration, policy and producer/model identity.

## Verification and limits

Untouched main baseline: 2334 passed, four expected optional-provider/PDF skips
outside the nested sandbox. Integrated suite: 2447 passed, four expected skips.
Ruff, strict mypy (600 files), ownership audit (322 historical rows), wheel/sdist
verification and a clean core install without Jev passed. Three additional
regressions cover actual PageIndex Markdown ending with a newline; the focused
index/semantic suite passes 44 tests. The Jev CI lane exercises the real SDK
through MockTransport. CI and automatic GitHub review remain pending publication.

Production quality gates remain unmeasured. No representative human gold set,
accepted calibrated thresholds or authorization for paid provider evaluation was
provided. Unit tests prove correctness boundaries, not study recall or provider
performance. No source uploads, model spend, deployments or merges occurred.

See [ADR-0021](../../docs/decisions/ADR-0021--bounded-judgement-and-derived-document-index.md).
