# Bounded judgement and shared document indexing

Implemented in Cardine, published in [PR #10](https://github.com/EbrahimAbdelwahed/cardine/pull/10).
The [original unified specification](unified-spec.md) remains the provenance
record. The owner's 2026-10-01 instructions supersede its TypeSafe SDK and
benchmark requirements: Jev uses OpenRouter API; benchmarks are excluded.

## Why the architecture differs from the original plan

Cardine has immutable SourceChunks and historical citations, rather than the
Harness KB v0.2 DocumentTree/RetrievableUnit/UnitId subsystem assumed by the plan.
`knowledge/unitizer.py` therefore binds identity-free drafts to existing chunks;
`ingestion.identity` remains the sole ChunkId creation authority. Runtime
classification uses whole canonical chunks before filtering. Classifying slices
and merging them afterward would reintroduce excluded content and invalidate
unchanged worker commitments. Canonical IDs, replay and human proposal decisions
retain their existing authority.

The PageIndex coordinator owns one persisted `DocumentIndex`; navigation and
flashcard anchors consume it. The qualified worker indexes admitted normalized
Markdown/text. For PDF it verifies original bytes and exact page provenance,
then indexes the admitted Markdown without another extractor. Historical PDFs
without an exact page map report an unavailable index. Index/cache state remains
derived and cannot append canonical events. ON has no hidden legacy semantic parser fallback. Flashcard tutor requests
require ON; OFF/SHADOW do not advertise that capability.

The optional Jev adapter uses OpenRouter's [Decisions REST protocol](https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request),
separate from chat completions. Requests use a configured model alias; semantic
analysis pins the resolved model version explicitly. This prevents alias changes
from silently sharing cached judgements. Credentials remain server-owned
environment references and every Jev call observes existing course consent.

Only CORE/SUPPORTING whole chunks reach deterministic planning. Weak judgement,
provider failure and incomplete excerpts preserve content. The selected lesson
scope cannot expand to another lesson. Cached analysis validates canonical
candidates, provenance and policy, and failures do not permanently poison it.

Tutor ON routes legal decisions, advertised capabilities/tools and closed
answers through Jev. Generation receives only the chosen payload schema; existing
validators and runner retain execution authority. General tutor model routing remains confined to OFF/SHADOW and explicitly
configured emergency fallback; it cannot bypass the flashcard scope contract.

The owner-requested 2026-10-05 repair replaces the route-then-capability cascade
with one flat Jev choice over concrete actions described by the versioned routing
guidance, limits the tutor to one clarification in a row, binds ON-fallback
flashcard routes through Jev's scope contract and records fallback failures. See
[ADR-0027](../../../docs/decisions/ADR-0027--useful-jev-routing-and-bounded-clarification.md).

## Configuration and code pointers

The owner-requested 2026-10-04 repair supplies bounded recent conversation and
same-turn tool observations to routing and the selected payload. Context cannot
authorize source evidence or actions. Topic flashcards select current canonical
chunks before planning; references to the latest explanation resolve its original
source locators. Valid source uncertainty settles with an evidence-limit message.
See [ADR-0025](../../../docs/decisions/ADR-0025--current-topic-and-context-settlement.md)
for bounds, retry behavior and the unchanged human decision boundary.

Repository configuration v2 separates providers, operations and consumer policy.
V1 reads migrate explicitly to OFF without rewriting the file. Conservative
thresholds are configurable policy choices; they are not calibrated claims.
[The complete configuration example](configuration.example.json) enables all ON
paths with only environment names. Install `.[jev]`, provide the referenced
server credentials, grant course consent, and reconcile indexing before lesson
selection. Existing repository files are not changed by this implementation.

Start from `study_agent.repository_config`, `cardine.cli.repository.LocalRepository`,
`cardine.application.study_semantics`, `study_agent.flashcards.semantic`,
`cardine.hosts.routing`, and `cardine.adapters.pageindex.coordinator`.
The planner, worker and canonical evidence resolver remain their existing owners.

## Large admitted documents

The qualified local structural worker admits up to 1,024 headings/nodes,
plus the adapter-owned synthetic document root. The 2 MiB input/output,
16,384-line, depth and killable timeout bounds remain enforced. The adapter
configuration identity includes this capacity so an old limited index is rebuilt.
Enabling document indexing requires reconciliation to READY before lesson selection.

## Verification and delivery

Runtime commit `92388de`: 2563 offline tests passed; four expected optional/live
smoke skips. Ruff, strict mypy (606 files), 322-row ownership audit, uv lock,
wheel/sdist verification and clean provider-free wheel imports passed. Tests in
`tests/unit/application/test_study_semantics.py` verify the real repository gate,
canonical evidence, selected scope, cache across reopen, provider failures,
consent and production routing. PDF/locator, unitizer/replay and router/runner
contracts have dedicated offline tests.

No benchmark, paid provider call, study-store migration, deployment or merge was
performed. Current-head GitHub CI and automatic review remain external delivery
checks, separate from implementation and local verification.

See [ADR-0021](../../../docs/decisions/ADR-0021--bounded-judgement-and-derived-document-index.md).

Owner completion continuation (2026-10-04): topic extraction shares routing's
supported action vocabulary, removes polite request wording, and bounds the
result to RetrievalQuery's 512-character contract while preserving token ends.
Canonical lifetime filtering precedes index/search limits; a production-path
retirement regression verifies that stale derived results cannot hide active
evidence. No additional semantic review or live rollout is implied.


The subsequent closed-scope repair supersedes topic extraction by regex/stopwords
in tutor ON. Jev chooses scope and profile, checks the query against the current
request, and the host consumes the resulting turn-bound contract without
reinterpreting learner language. The duplicate flashcard language interpreter is deleted; no compatibility path
accepts a free-form scope. See the
closed-scope continuation in [ADR-0025](../../../docs/decisions/ADR-0025--current-topic-and-context-settlement.md).
This changes flashcard request interpretation, not retrieval adjacency or the
indexing and grounding outcomes owned by other tasks.

## Optional post-draft documentary support

Cardine's `application/flashcard_grounding.py` wraps hybrid/morphology integrity
validation after deterministic checks and before proof or proposal registration.
`features.flashcard_grounding_mode` defaults to OFF for existing v1/v2 files.
ON requires complete support for question premises, labels, all answer blocks
and key points against only that card's canonical cited excerpts; SHADOW keeps
historical proposal behavior. Configure positive `grounding_probability` (0.9)
and `grounding_margin` (0.2), plus the resolved judgement model and timeout.
Whole requests exceeding 64,000 UTF-8 bytes fail closed; sources are never sliced.
Errors, weak choices, contradiction and insufficient evidence remain unverified.
This text-only gate rejects media claims. HUMAN acceptance remains separate.
See the [integration handoff](../../../dev/logs/2026-10-04--cardine--flashcard-grounding-gate--log.md)
for checkpoint isolation, consent, tests and sibling-task integration seams.
