# Study Agent Harness — Unified Jev + PageIndex Refactor Specification

Status: implementation-ready consolidated spec

Purpose: replace the previous Jev integration spec plus later revision notes with one authoritative document.

---

## 0. Scope and precedence

This document is the single implementation specification for the Jev + PageIndex refactor of the Study Agent Harness.

It merges:
- the parts of the original integration spec that remain valid;
- the later architecture revisions;
- the codebase-specific refactor preparation already completed.

Where the earlier design and the later revision differ, this document contains only the final decision. The implementer should not need to compare multiple specs.

The guiding principle is:

> Jev may become important for system performance and semantic selection, but must not become important for canonical correctness, authority, provenance, or host lifecycle.

The final target has:
- one canonical source/revision substrate;
- one primary derived document index;
- one bounded semantic judgement primitive;
- one tutor semantic router;
- one UnitId authority;
- one host validation lifecycle.

---

# 1. Architectural goals

The refactor introduces Jev and PageIndex in two major areas.

## 1.1 Shared derived document structure and flashcard preprocessing

The document pipeline becomes:

```text
immutable SourceRevision
        ↓
PageIndex adapter
        ↓
provider-neutral DocumentIndex
        ↓
host-owned locator reconciliation
        ↓
derived candidates
        ↓
Jev bounded semantic classification
        ↓
SemanticLessonAnalysis
        ↓
CORE + SUPPORTING projection
        ↓
LessonGenerationUnit
        ↓
existing flashcard planner and worker pipeline
```

Goals:
- PageIndex becomes the primary derived document structure.
- Jev classifies bounded PageIndex-derived candidates.
- material that is not card-worthy is removed before the generative flashcard model sees it;
- canonical source/revision identity and evidence spans remain owned by the Harness;
- the existing deterministic flashcard planner and downstream worker remain unchanged.

## 1.2 Tutor semantic routing

The tutor pipeline becomes:

```text
TutorHostContext
      ↓
host-owned routing projection
      ↓
ChoiceJudgementPort
      ↓
Jev semantic route selection
      ↓
route-specific payload assembly
      ├── deterministic assembly where possible
      └── ModelPort structured generation where needed
      ↓
existing TutorDecision type
      ↓
existing validate_decision()
      ↓
existing TutorHostRunner lifecycle
```

Goals:
- Jev is the single semantic tutor router;
- Jev chooses only among bounded legal routes/options;
- Jev does not generate arbitrary learner-facing text or arbitrary tool payloads;
- the generative model is used after routing, only for the payload required by the already chosen route;
- the existing host validation, stale-state handling, capability execution, continuation lifecycle, retries, and authority remain unchanged.

---

# 2. Core architectural invariants

The following are non-negotiable.

1. `SourceRevision` and the frozen source substrate remain canonical truth.
2. PageIndex is a rebuildable derived projection, not canonical state.
3. Jev outputs are derived semantic judgements, not authority.
4. PageIndex node IDs are never canonical source, citation, topic, unit, or action identity.
5. Jev never creates trusted fingerprints, UnitIds, capability authority, action IDs, or continuation fingerprints.
6. Evidence is grounded through host-owned source/revision locators/spans.
7. The flashcard planner remains deterministic.
8. The flashcard worker remains unaware of Jev and PageIndex.
9. `TutorHostRunner` remains unaware of Jev and provider-specific logic.
10. Every tutor decision still passes through the existing `validate_decision()`.
11. Provider SDKs remain behind provider-specific adapters.
12. Core installation must work without Jev or PageIndex dependencies installed.
13. Derived semantic analysis and index caches are operational/derived state, not canonical events.

---

# 3. Final decision on DocumentTree vs PageIndex

The previous design treated PageIndex as a semantic prior alongside the existing deterministic `DocumentTree`.

That is no longer the target.

## 3.1 Final target

PageIndex becomes the primary derived document structure:

```text
SourceRevision
    ↓
DocumentIndex
    ↓
downstream derived features
```

The existing deterministic `DocumentTree` path is migration/compatibility code, not a second permanent semantic representation.

The desired end state is not:

```text
SourceRevision
  +-- Cardine DocumentTree
  +-- Cardine lesson/topic tree
  `-- PageIndex tree
```

It is:

```text
SourceRevision
  `-- PageIndex / DocumentIndex
         `-- Jev semantic selection
```

## 3.2 Migration behavior

Do not delete `DocumentTree` immediately.

During shadow migration:

```text
                     ┌→ legacy DocumentTree → legacy units/results
SourceRevision ──────┤
                     └→ PageIndex → DocumentIndex → candidate new path
```

Compare:
- source coverage;
- span validity;
- retrieval quality;
- grounding quality;
- flashcard quality;
- replay and historical compatibility.

Only after the new path passes the grounding and feature benchmarks should the legacy tree be removed from the primary runtime.

## 3.3 PageIndex failure

Once PageIndex is the primary path:

- retain the canonical `SourceRevision`;
- surface/retry the indexing failure for structure-dependent features;
- do not silently fall back to a second semantic parser.

A silent legacy semantic fallback would recreate the duplication this refactor is intended to remove.

---

# 4. Shared low-level semantic primitive: ChoiceJudgementPort

Flashcard semantic selection and tutor routing share only one generic need:

> Given bounded state and a finite closed set of choices, return a selected choice plus the distribution over all choices.

This is the only Jev abstraction that should be shared.

Create:

```text
src/study_agent/ports/judgement.py
```

Recommended contract name:

```text
ChoiceJudgementPort
```

Do not call it `JevPort`.

Jev is a provider. The domain requires bounded choice judgement.

## 4.1 Request shape

Conceptually:

```python
@dataclass(frozen=True, slots=True)
class ChoiceOption:
    key: str
    description: str

@dataclass(frozen=True, slots=True)
class ChoiceJudgementRequest:
    instruction: str
    state: JsonValue
    options: tuple[ChoiceOption, ...]
    metadata: JsonObject
```

## 4.2 Result shape

Conceptually:

```python
@dataclass(frozen=True, slots=True)
class ChoiceProbability:
    key: str
    probability: float

@dataclass(frozen=True, slots=True)
class ChoiceJudgement:
    selected_key: str
    probabilities: tuple[ChoiceProbability, ...]
    confidence: float | None
    producer_id: str
    producer_version: str
    model_id: str
    latency_ms: float | None
    usage: JsonObject
```

The port:

```python
class ChoiceJudgementPort(Protocol):
    async def judge(
        self,
        request: ChoiceJudgementRequest,
    ) -> ChoiceJudgement: ...
```

## 4.3 Port validation

The provider-neutral layer must reject malformed judgement results:
- fewer than two options;
- duplicate option keys;
- selected key not present in options;
- missing probability entries;
- unknown probability keys;
- NaN/negative/non-finite probabilities;
- invalid normalization outside a small defined tolerance.

The domain must not trust a malformed adapter result.

## 4.4 What the port must not know

The port must not know:
- `LessonParagraph`;
- `SemanticTopic`;
- `TutorHostContext`;
- capability IDs as a special concept;
- PageIndex;
- flashcard eligibility;
- threshold policy;
- routing policy.

The provider answers a bounded choice question.

The consumer decides what the answer means.

## 4.5 Do not reuse ModelPort for this

`ModelPort` models:
- generation;
- structured output;
- streaming;
- tool-call-capable model behavior.

It does not model a full probability distribution over a finite closed option set.

Adding Jev-specific probability semantics to `ModelPort` would make a clean generic model contract worse.

A separate small `ChoiceJudgementPort` is the narrower abstraction.

## 4.6 Do not implement the entire TypeSafe primitive set

V1 supports `Choice` only.

Boolean is a two-choice instance.

Do not pre-implement unrelated primitive types without a real Harness use case.

---

# 5. One Jev adapter for the whole codebase

Create:

```text
src/study_agent/adapters/judgement/jev.py
```

This is the only provider-specific Jev adapter.

Do not create:
- `JevFlashcardAdapter`;
- `JevTutorAdapter`;
- `JevTopicAdapter`;
- `JevCardabilityAdapter`.

The single adapter owns:
- authentication;
- endpoint/SDK;
- request serialization;
- timeout;
- transport retry;
- response parsing;
- probability normalization;
- provider protocol errors;
- provider/model metadata;
- latency/usage capture.

It must not own:
- flashcard prompts/policies;
- cardability thresholds;
- topic semantics;
- capability routing semantics;
- fallback to the full tutor;
- host validation.

## 5.1 Retry ownership

The Jev adapter owns transport retry only.

It must not contain domain retries such as:
- retry until `CORE`;
- retry until route confidence is high;
- retry until topic boundary changes.

Semantic fallback belongs to the consumer.

## 5.2 Concurrency

Independent candidate classifications should use shared bounded concurrency.

Starting operational default:

```text
JEV_CONCURRENCY=64
```

Requirements:
- configurable;
- shared budget per process/event loop, not one semaphore per document;
- deterministic association between candidate and result;
- provider retry does not silently drop candidates;
- benchmark 16 / 32 / 64 or similar values;
- concurrency is operational configuration, not semantic policy.

---

# 6. Provider-neutral DocumentIndex

The previous proposal used an `OutlinePort`.

That is no longer sufficient because PageIndex becomes the shared document structure across features.

Create:

```text
src/study_agent/domain/document_index.py
src/study_agent/ports/document_index.py
src/study_agent/adapters/document_index/pageindex.py
src/study_agent/knowledge/document_index.py
```

---

# 7. `domain/document_index.py`

This module owns immutable provider-neutral derived document-index value types.

Recommended conceptual types:

```python
class LocatorKind(StrEnum):
    PDF_PAGE_RANGE = ...
    MARKDOWN_LINE_RANGE = ...
    TEXT_SPAN = ...
```

```python
@dataclass(frozen=True, slots=True)
class SourceLocator:
    kind: LocatorKind
    start_page: int | None = None
    end_page: int | None = None
    start_line: int | None = None
    end_line: int | None = None
    start_offset: int | None = None
    end_offset: int | None = None
```

```python
@dataclass(frozen=True, slots=True)
class DocumentNode:
    node_key: str
    parent_key: str | None
    title: str | None
    summary: str | None
    locator: SourceLocator
    order: int
    children: tuple[str, ...]
```

```python
@dataclass(frozen=True, slots=True)
class DocumentIndex:
    source_id: SourceId
    revision_id: RevisionId
    index_version: str
    producer_id: str
    producer_version: str
    config_fingerprint: str
    nodes: tuple[DocumentNode, ...]
    fingerprint: str
```

## 7.1 Invariants

Validate:
- one root;
- unique node keys;
- every parent exists;
- acyclic graph/tree;
- deterministic ordering;
- children and parent references agree;
- valid locator shape per locator kind;
- complete deterministic index fingerprint.

## 7.2 What DocumentIndex must not contain

No:
- PageIndex SDK types;
- Jev output;
- flashcard cardability;
- `LessonTopic`;
- trusted canonical citations;
- provider cloud document IDs as canonical identity.

A PageIndex node key is derived index identity only.

---

# 8. `ports/document_index.py`

The port models:

```text
immutable revision material
→ derived DocumentIndex
```

Conceptually:

```python
@dataclass(frozen=True, slots=True)
class DocumentIndexRequest:
    source_id: SourceId
    revision_id: RevisionId
    media_type: str
    content: bytes
    normalized_text: str | None
    metadata: JsonObject

class DocumentIndexPort(Protocol):
    async def build(
        self,
        request: DocumentIndexRequest,
    ) -> DocumentIndex: ...
```

The port must remain independent from:
- local temp-file implementation;
- PageIndex cloud IDs;
- provider SDK objects.

---

# 9. `adapters/document_index/pageindex.py`

This is the only PageIndex provider boundary.

Responsibilities:
- SDK/API import;
- authentication;
- PageIndex invocation;
- provider-specific file handling;
- timeout/retry;
- parsing raw provider output;
- normalization into `DocumentIndex`.

Recommended private helpers:

```python
_normalize_document(...)
_normalize_node(...)
_normalize_locator(...)
_provider_metadata(...)
```

Raw PageIndex data must not circulate through the rest of the codebase.

---

# 10. Source locators are navigation/provenance metadata, not authority

PageIndex may expose:
- page ranges;
- line ranges;
- node IDs;
- summaries.

These are not automatically canonical evidence.

The invariant is:

> Downstream grounded features commit to the immutable source/revision and a host-verifiable locator/span, not to a PageIndex node ID.

Create deterministic reconciliation in:

```text
src/study_agent/knowledge/document_index.py
```

Responsibilities:
- validate a `DocumentIndex` against source/revision context;
- resolve locator → canonical span where possible;
- create candidate node views;
- create identity-free unit drafts during migration.

Recommended functions:

```python
resolve_locator(...)
resolve_node_span(...)
candidate_nodes(...)
unit_drafts_from_document_index(...)
```

No guessing.

A locator that cannot be reconciled must return an explicit failure result.

---

# 11. PDF page ranges → canonical TextSpan

The current substrate already owns a deterministic `page_map` from page number to normalized Unicode offset.

Use that to reconcile PageIndex page ranges.

For a node spanning pages 18–20:

```text
start = page_map(page 18).offset
end   = page_map(page 21).offset
```

If page 20 is the final page:

```text
end = substrate.character_length
```

Validate:
- page exists;
- order is valid;
- source/revision matches;
- resulting span is inside substrate bounds.

The resulting `TextSpan` is host-owned evidence.

The original PageIndex page range remains derived navigation metadata.

---

# 12. Markdown/text locators

For Markdown line locators:
- derive newline offsets deterministically from the normalized substrate;
- map line range to Unicode codepoint offsets;
- validate bounds;
- resolve to canonical span.

For text offset locators:
- verify offset bounds;
- verify substrate binding;
- materialize canonical `TextSpan`.

---

# 13. Unitizer migration

The current `knowledge/unitizer.py` is both:
1. structural draft builder from `DocumentTree`;
2. the single owner of final UnitId creation/materialization.

Do not create a second PageIndex unitizer.

Refactor the existing unitizer so structural drafting and identity materialization are separate.

Target shape:

```python
draft_units_from_legacy_tree(...)
draft_units_from_document_index(...)

materialize_unit_drafts(
    text,
    drafts,
    revision_id,
    binding,
    policy,
    meta,
)
```

Keep `_unit_id(...)` in `knowledge/unitizer.py` as the only UnitId authority.

During migration, the old:

```python
unitize(text, tree, ...)
```

can remain as a compatibility wrapper around:

```python
draft_units_from_legacy_tree(...)
→ materialize_unit_drafts(...)
```

The new path is:

```text
DocumentIndex
→ unit_drafts_from_document_index(...)
→ materialize_unit_drafts(...)
```

After migration, remove the legacy drafting path from the primary runtime.

---

# 14. RetrievableUnit remains structurally compatible

Do not add:
- PageIndex node ID;
- Jev confidence;
- semantic topic;
- cardability;
- PageIndex summary;

to `RetrievableUnit`.

The existing canonical unit shape is already correct:
- revision-local identity;
- canonical reference;
- structural path;
- immutable metadata;
- links.

The DocumentIndex migration should not mutate that persisted contract unless a separately justified schema migration becomes necessary.

If a new unitizer algorithm changes unit IDs, use an explicit new unitizer version while preserving historical resolution/replay.

---

# 15. Flashcard semantic layer

Create:

```text
src/study_agent/flashcards/semantic.py
```

Do not create a top-level `semantic/` package yet.

The concept being introduced is still specifically a pedagogical flashcard-generation projection over the shared `DocumentIndex`.

This module owns:
1. flashcard semantic value types;
2. candidate classification policy;
3. pedagogical topic-anchor selection;
4. semantic analysis receipt;
5. projection into the existing `LessonGenerationUnit`.

It must contain no provider imports.

---

# 16. Do not turn LessonParagraph into a universal semantic object

`LessonParagraph` is a generation-planning value type.

Do not add:
- Jev confidence;
- PageIndex node ID;
- descriptor;
- summary;
- cardability;
- provider identity.

Instead introduce a separate semantic analysis type, e.g.:

```text
SemanticLessonParagraph
```

or, preferably in the new PageIndex-based path:

```text
SemanticCandidate
```

Conceptual fields:

| Field | Role |
|---|---|
| candidate/node key | derived index identity |
| lesson/source scope | ownership |
| relative position | deterministic order |
| canonical span | source authority |
| descriptor | navigation/debug metadata |
| document path | structural prior |
| pedagogical topic anchor | semantic projection |
| cardability | CORE/SUPPORTING/CONTEXT_ONLY/EXCLUDED |
| probabilities | audit/eval |
| judgement producer | provenance |

Raw source text should not be duplicated permanently.

Resolve it from canonical evidence when classifying.

---

# 17. Candidate descriptor

Do not introduce a second generative model merely to summarize every paragraph/node.

Preference order:

1. sufficiently local PageIndex summary;
2. node heading + first informative sentence;
3. first non-empty informative sentence;
4. structural label/title fallback.

Descriptor use:
- Jev input;
- debugging;
- eval/telemetry.

Descriptor is not evidence and must never substitute for the canonical source span.

---

# 18. Definition of pedagogical topic

Keep one versioned topic definition:

> A topic is the smallest autonomous conceptual unit of the lesson that represents a recognizable study object and may contain phases, components, examples, mechanisms, or subordinate details without changing conceptual identity.

Example:

```text
Biochemistry                 above topic
Metabolism                   above topic
Glucose metabolism           above topic
Glycolysis                   topic
Preparatory phase            below topic
Hexokinase reaction          below topic
Individual enzyme/step       below topic
```

The definition must have:
- semantic policy ID;
- version;
- fingerprint.

Changing this definition invalidates semantic-analysis cache.

---

# 19. PageIndex structure vs pedagogical topic projection

Do not copy the PageIndex tree into `LessonTopic`.

PageIndex is the shared structural index.

The flashcard semantic layer selects pedagogically meaningful anchors from that tree.

Example:

```text
PageIndex
Glucose metabolism
  Glycolysis
    Preparatory phase
      Hexokinase
      PGI
      PFK-1
    Payoff phase
```

Semantic projection:

```text
Topic anchor: Glycolysis
eligible candidates:
  ...
```

`Preparatory phase` can remain in the structural path without becoming an independent flashcard topic.

The semantic layer may maintain a flat ordered set of pedagogical topic anchors, but must not construct a second competing hierarchical document tree.

---

# 20. Topic-anchor selection

The original sequential `SAME_TOPIC / NEW_TOPIC` algorithm is not the primary path.

Do not default to:

```text
current topic + next paragraph → SAME_TOPIC / NEW_TOPIC
```

Instead:
1. PageIndex determines candidate hierarchy and source placement.
2. The flashcard semantic layer identifies the meaningful pedagogical anchor in the relevant local ancestor path.
3. Jev is used only where that bounded choice is semantically ambiguous.

Example bounded choice:

```text
ABOVE_TOPIC
TOPIC
BELOW_TOPIC
```

or directly:

```text
ancestor_A
ancestor_B
ancestor_C
```

No global clustering and no second Cardine-authored topic tree.

---

# 21. Topic identity

Jev never generates `topic_key`.

Topic identity is host-generated and deterministic.

Derive it conceptually from:
- revision/lesson identity;
- `DocumentIndex` fingerprint;
- selected anchor position/locator;
- semantic topic policy version.

Do not use:
- topic title;
- Jev prose;
- PageIndex cloud document ID.

Titles may change.

Identity must remain tied to the reproducible semantic analysis input.

---

# 22. Card-worthiness is a separate judgement

Topic membership/granularity and card-worthiness remain separate questions.

Jev cardability labels:

| Label | Meaning |
|---|---|
| CORE | concept that should be actively retrievable |
| SUPPORTING | useful secondary knowledge that is still card-worthy |
| CONTEXT_ONLY | useful for understanding but not an autonomous recall target |
| EXCLUDED | boilerplate, administrative text, transition, duplicate, or noise |

Classify candidates using only the necessary local context:
- canonical node/paragraph text or bounded excerpt;
- local title/path;
- selected topic anchor/title;
- descriptor;
- relative position.

Do not pass the whole lesson unless an eval demonstrates that it is needed.

---

# 23. Do not classify topic card-worthiness separately

Topic-level eligibility derives deterministically from candidate labels.

Recommended mapping:

```text
>= 1 CORE
    → ELIGIBLE / CORE

no CORE, >= 1 SUPPORTING
    → ELIGIBLE / SUPPORTING

no eligible candidate, >= 1 CONTEXT_ONLY
    → CONTEXT_ONLY / NONE

all EXCLUDED
    → EXCLUDED / NONE
```

This maps directly to the planner's existing:
- `PlanningEligibility`;
- `PlanningPriority`.

No second Jev call is needed.

---

# 24. Do not modify the deterministic flashcard planner

This remains one of the strongest parts of the original design.

The semantic layer filters before the planner.

The planner should receive a normal `LessonGenerationUnit` whose generation scope contains only:
- CORE;
- SUPPORTING.

The current planner then continues to perform deterministic:
- topic ordering;
- bundle construction;
- slot construction;
- splitting/budget behavior;
- planning receipts/fingerprints.

No Jev logic enters `planning.py`.

---

# 25. Materializing LessonGenerationUnit

The semantic analysis keeps all candidates.

Example:

```text
Semantic topic: Glycolysis
 p1 CORE
 p2 CORE
 p3 CONTEXT_ONLY
 p4 SUPPORTING
 p5 EXCLUDED
```

The planner projection becomes:

```text
LessonGenerationUnit: Glycolysis
 p1
 p2
 p4
```

`p3` and `p5` remain in `SemanticLessonAnalysis`.

They do not enter generative evidence scope.

## 25.1 LessonTopic projection

Create normal `LessonTopic` values with:
- semantic topic identity;
- display title;
- deterministic source position;
- resolved source span;
- derived eligibility;
- derived priority;
- paragraph keys containing only generation-eligible candidates.

## 25.2 LessonParagraph projection

Create normal `LessonParagraph` values only for:
- CORE;
- SUPPORTING.

Do not add semantic-analysis metadata to `LessonParagraph`.

---

# 26. CONTEXT_ONLY policy for V1

Do not send raw `CONTEXT_ONLY` material to the generator in V1.

Current downstream contracts do not explicitly distinguish:
- generation target;
- context-only evidence.

Sending it would make it possible for the generative model to produce cards from material that was meant only as support.

V1 behavior:

```text
CORE          → generator
SUPPORTING    → generator
CONTEXT_ONLY  → semantic analysis/index only
EXCLUDED      → semantic analysis/index only
```

If evals later demonstrate a quality loss from missing context, add an explicit contextual-evidence contract in a separate change.

---

# 27. Flashcard worker remains completely unaware of Jev

Do not add any of the following to worker requests:
- Jev classification;
- PageIndex path;
- topic confidence;
- excluded candidates;
- provider metadata.

The worker receives a normal trusted plan and a normal trusted scope.

Existing worker and scope enforcement remain the grounding/security boundary.

A correct integration should be invisible to the worker.

---

# 28. SemanticLessonAnalysis

The semantic preprocessing output must be immutable and fingerprinted.

Recommended name:

```text
SemanticLessonAnalysis
```

It contains at minimum:

## Input identity
- source/lesson identity;
- ordered revision identities;
- canonical input fingerprint;
- candidate extraction/version fingerprint.

## Document index identity
- `DocumentIndex` fingerprint;
- PageIndex producer/version/config fingerprint.

## Policy identity
- semantic analyzer ID;
- analyzer version;
- topic-definition fingerprint;
- cardability-definition fingerprint;
- threshold policy fingerprint.

## Judgement provenance
- judgement provider;
- Jev model identity/version;
- optional API/model version.

## Semantic output
- ordered analyzed candidates;
- resolved canonical spans;
- pedagogical topic anchors;
- cardability labels;
- full probability distributions;
- provider confidence;
- top-1/top-2 margin;
- accepted/fallback status;
- fallback reason.

## Final identity
- analysis fingerprint.

The analysis receipt is derived and discardable.

It is not canonical event history.

---

# 29. Cache and one-time processing

Semantic preprocessing and document indexing are rebuildable derived work.

They must not become canonical events such as:

```text
JevClassifiedParagraph
PageIndexNodeCreated
```

## 29.1 DocumentIndex cache key

Equivalent to:

```text
source/revision identity
+ canonical source fingerprint
+ normalization contract
+ PageIndex adapter/version
+ PageIndex model/config fingerprint
```

## 29.2 SemanticLessonAnalysis cache key

Equivalent to:

```text
canonical lesson/source fingerprint
+ DocumentIndex fingerprint
+ semantic policy fingerprint
+ Jev model identity/version
```

Do not use:
- lesson ID alone;
- filename;
- cloud PageIndex document ID;
- lesson title.

## 29.3 Storage

Use operational/derived storage.

Do not append canonical events for cache creation/invalidation.

A small content-addressed derived-analysis store can be introduced separately, e.g.:

```text
ports/derived_analysis.py
adapters/sqlite/derived_analysis.py
```

The semantic analyzer should still work with a null/no-op cache.

---

# 30. Flashcard failure semantics

The feature must remain conservative against content loss.

## Jev unavailable/fails during cardability
Keep the candidate.

Do not treat failure as `EXCLUDED`.

## Ambiguous classification
Prefer retention.

## Strong EXCLUDED judgement
Drop only when Harness policy accepts the distribution/margin.

## Weak EXCLUDED judgement
Keep.

## CORE/SUPPORTING ambiguity
Keep.

The most expensive error is false exclusion because the generator can no longer recover the missing source material.

## PageIndex unavailable
Different semantics:
- keep the canonical source revision;
- mark document indexing unavailable/failed;
- retry or report structure-dependent feature failure;
- do not silently run a second permanent semantic parser in ON mode.

---

# 31. Using Jev probabilities

Never turn provider confidence directly into authority.

Store:
- full probability distribution;
- selected winner;
- generic confidence if provided;
- top-1/top-2 separation.

Thresholds belong to Harness policy.

The Jev adapter must never contain domain code such as:

```python
if confidence > 0.83:
    exclude()
```

---

# 32. Flashcard threshold policy

Create an immutable semantic policy/config.

It may eventually contain separate thresholds for:
- accepting a pedagogical anchor/granularity judgement;
- accepting `EXCLUDED`;
- accepting `CONTEXT_ONLY`;
- minimum margin.

Exclusion must be the most conservative.

Do not guess final numerical values in architecture code.

Choose them from eval data.

All values participate in the semantic policy fingerprint.

---

# 33. Tutor routing — final architecture

The old idea was a narrow optional fast path in front of the existing full tutor decision model.

The final design is different.

Jev is the single semantic route selector.

The sequence is:

```text
host constraints
    ↓
allowed bounded routes
    ↓
Jev Choice
    ↓
chosen route
    ↓
route-specific payload construction
    ↓
existing TutorDecision
    ↓
existing validate_decision()
```

The large model is not a second semantic router in the normal path.

It is used only for route-specific payload generation where deterministic assembly is insufficient.

---

# 34. New tutor component

Create:

```text
src/study_agent/hosts/routing.py
```

Recommended class:

```text
RoutingTutorDecisionPort
```

It implements the existing `TutorDecisionPort`.

Dependencies:
- `ChoiceJudgementPort`;
- `ModelPort`;
- optional legacy `TutorDecisionPort` for shadow/emergency fallback;
- immutable routing policy/config.

It must not depend on:
- concrete Jev adapter;
- concrete OpenAI tutor adapter;
- capability gateway internals.

---

# 35. Return existing tutor decision types

Do not create public runner-facing:
- `JevDecision`;
- `FastDecision`;
- `RouterDecision`.

Internally, `hosts/routing.py` may have a private `TutorRoute` enum.

The runner must still receive one of:
- `StartCapabilityDecision`;
- `AnswerDialogueDecision`;
- `AskLearnerDecision`;
- `AssistantMessageDecision`;
- `StopDecision`.

This preserves the existing validator and lifecycle.

---

# 36. Routing context minimization

Do not pass the entire `TutorHostContext` to Jev by default.

Build a transient non-authoritative routing projection.

Minimum useful fields:

## General
- latest learner utterance;
- minimal session/mode information;
- legal route choices.

## Pending continuation
- dialogue request/question;
- response schema category;
- closed options where applicable.

## Capability routing
- advertised capability IDs;
- compact descriptions;
- minimal information needed to distinguish them.

Do not automatically send:
- full learner evidence;
- entire tutor snapshot;
- all host files;
- complete capability schemas;
- irrelevant history.

---

# 37. Deterministic route narrowing is not semantic routing

Host constraints may remove impossible routes before Jev.

Examples:
- a route is illegal while a continuation is pending;
- a capability is not advertised;
- the host contract forbids a route in the current lifecycle state.

This is authority/schema narrowing, not semantic interpretation.

If exactly one legal route remains because of host constraints, selecting it directly is acceptable because no semantic choice remains.

Do not reintroduce a parallel deterministic NLP routing ruleset.

---

# 38. Route taxonomy

Internal bounded route choices:

```text
ANSWER_DIALOGUE
START_CAPABILITY
ASK_LEARNER
ASSISTANT_MESSAGE
STOP
```

Jev answers:

> Which legal route should the tutor take next?

It does not:
- generate prose;
- create clarification text;
- construct arbitrary tool arguments;
- write canonical state;
- execute capabilities;
- authorize actions;
- bypass schema validation;
- bypass stale-state handling;
- bypass idempotency.

---

# 39. Pending continuation

This remains the safest first executable route.

## 39.1 Closed boolean/enum/literal response

Flow:

```text
Jev route → ANSWER_DIALOGUE
        ↓
bounded Jev choice over legal response values
        ↓
AnswerDialogueDecision
```

The trusted continuation fingerprint is copied from `TutorHostContext`.

Jev never generates it.

## 39.2 Free string or complex response schema

Jev still chooses the `ANSWER_DIALOGUE` route.

Then `ModelPort.generate()` receives only:
- dialogue request;
- latest learner utterance;
- required response schema;
- minimal trusted context.

The structured output is the dialogue payload only.

The generative model does not emit a full `TutorDecision`.

---

# 40. Capability selection

For `START_CAPABILITY`, Jev selects only among currently advertised capabilities.

Choice keys should be opaque/stable capability IDs.

Descriptions are built from the advertised capability metadata.

Jev cannot choose a capability that is not advertised.

Separate:

```text
capability selection
```

from:

```text
argument binding
```

This is critical.

Do not turn Jev into a general-purpose tool-calling model.

---

# 41. Capability input binding

## 41.1 Deterministic cases

No model call if:
- input schema accepts `{}`;
- all values are fixed/defaultable;
- all required values already exist in trusted bounded context.

Construct `StartCapabilityDecision` directly.

## 41.2 Semantic binding cases

Once Jev has selected one capability, use `ModelPort` with:
- latest learner utterance;
- chosen capability description;
- chosen capability input schema;
- minimal trusted context.

Structured output schema must be exactly the chosen capability input schema.

Do not show the generative model:
- other capabilities;
- the full TutorDecision schema.

This prevents the model from redoing routing.

---

# 42. AssistantMessageDecision and AskLearnerDecision

Jev chooses the route.

Jev does not generate the message.

After route selection:

## Assistant message

`ModelPort` generates only:

```json
{"content":"..."}
```

or the narrow equivalent required by the existing contract.

## Ask learner

`ModelPort` generates only the clarification/question payload.

This avoids:

```text
Jev → "assistant_message" → full GPT TutorDecision
```

which would repeat routing and add unnecessary complexity.

---

# 43. StopDecision

If a valid stop decision can be constructed deterministically after route selection, do so.

Do not call a generative model just to serialize an already bounded stop.

If multiple bounded stop reasons matter semantically, a small choice judgement may be used, but only if the domain actually requires it.

---

# 44. Existing validation remains the final authority

Every path ends with the existing host validation.

Conceptually:

```python
decision = await routing_port.decide(context)
validate_decision(context, decision)
```

Do not bypass:
- capability advertisement checks;
- input schema validation;
- pending continuation checks;
- continuation fingerprint checks;
- stale state checks;
- lifecycle rules.

The architecture is correct only if Jev can be wrong and host invariants still hold.

---

# 45. Tutor shadow mode

Before Jev becomes authoritative:

For each routable tutor turn:

1. run the new routing pipeline;
2. produce a candidate normal `TutorDecision`;
3. run the current full tutor `TutorDecisionPort`;
4. keep the legacy decision authoritative;
5. compare the two paths.

Record:
- Jev route;
- full distribution;
- confidence/margin;
- routed candidate decision class;
- legacy decision class;
- disagreement;
- Jev latency;
- narrow payload-generation latency;
- legacy full-model latency;
- validation result;
- fallback reason.

Shadow data is eval/telemetry only.

It is not canonical state or authority.

---

# 46. Tutor failure semantics

## Jev timeout/provider failure
During shadow/migration:
- delegate once to the legacy full `TutorDecisionPort`.

After the routing path becomes primary:
- use configurable emergency/kill-switch fallback if retained.

Never return `StopDecision(NO_SAFE_ACTION)` merely because Jev failed.

## Malformed Jev result
Fallback.

## Insufficient distribution separation
Fallback according to routing policy.

## Capability no longer advertised
Validation/freshness failure → fallback/re-evaluate with fresh context.

## Payload cannot be constructed
Fallback.

## Narrow ModelPort generation fails
Fallback to legacy full tutor if emergency fallback is enabled.

Do not build chains such as:

```text
Jev retry × 5
→ model retry × 5
→ another model
```

Interactive latency must remain bounded.

---

# 47. Reuse ModelPort for route-specific generation

Do not create a new provider-specific tutor payload adapter unless a later concrete problem requires it.

The existing generic `ModelPort` already supports:
- normal generation;
- structured output constraints;
- model identity;
- usage;
- generic provider error vocabulary.

The semantic meaning of the generation request belongs in `hosts/routing.py`.

Provider transport stays behind `ModelPort`.

The existing full OpenAI tutor-decision adapter remains useful as:
- shadow comparator;
- emergency fallback;
- migration reference.

Do not immediately rewrite it into the new payload generator.

---

# 48. Files to create

Required new files:

```text
src/study_agent/domain/document_index.py
src/study_agent/ports/document_index.py
src/study_agent/adapters/document_index/pageindex.py
src/study_agent/knowledge/document_index.py

src/study_agent/ports/judgement.py
src/study_agent/adapters/judgement/jev.py

src/study_agent/flashcards/semantic.py
src/study_agent/hosts/routing.py
```

Potential later operational cache files:

```text
src/study_agent/ports/derived_analysis.py
src/study_agent/adapters/sqlite/derived_analysis.py
```

Do not create the cache abstraction until persistence is actually wired.

---

# 49. Existing files to modify

## `src/study_agent/ports/__init__.py`

Export the new public provider-neutral contracts if the package continues to re-export all public ports.

## `src/study_agent/knowledge/unitizer.py`

Refactor structural drafting from final UnitId/materialization ownership.

Add the DocumentIndex-derived draft path while keeping a single identity owner.

## `src/study_agent/knowledge/units.py`

Minimal migration/version work only if required.

Do not rewrite canonical unit semantics.

## `src/study_agent/domain/tree.py`

Migration/deprecation documentation only initially.

Do not add PageIndex/Jev fields.

## `src/study_agent/knowledge/tree.py`

Keep for shadow/compatibility initially; remove from primary runtime after migration benchmark.

## `src/study_agent/repository_config.py`

When provider wiring reaches repository runtime:
- add judgement adapter config;
- add document-index adapter config;
- add semantic feature modes;
- bump strict config schema version;
- preserve credential-env references instead of storing secrets.

## `src/study_agent/demo/anatomy.py`

Replace concrete decision adapter exposure with normal `TutorDecisionPort` composition.

Wire:
- ChoiceJudgementPort;
- ModelPort;
- RoutingTutorDecisionPort;
- legacy full TutorDecisionPort for shadow/emergency.

## `examples/reference_tutor_host.py`

Update reference wiring.

## `pyproject.toml`

Keep core runtime dependencies empty/minimal.

Add provider optional extras, conceptually:

```toml
[project.optional-dependencies]
openai = [...]
jev = [...]
pageindex = [...]
```

Do not make Jev or PageIndex mandatory core requirements.

---

# 50. Files that should remain unchanged in the primary implementation

Do not modify unless a concrete blocker proves otherwise:

```text
src/study_agent/flashcards/planning.py
src/study_agent/ports/flashcard_planning.py
src/study_agent/flashcards/lesson_worker_contracts.py
src/study_agent/flashcards/lesson_worker_service.py
src/study_agent/flashcards/scope.py
src/study_agent/capabilities/hybrid_flashcards.py

src/study_agent/hosts/runner.py
src/study_agent/ports/tutor_host.py
src/study_agent/hosts/contracts.py

src/study_agent/adapters/host/openai_responses.py
src/study_agent/ports/model.py
```

The previous "minimal blast radius" principle still applies to these already-correct boundaries.

What changed is that the document-tree/unitizer boundary now legitimately requires migration work.

---

# 51. Architecture tests

Add or extend architecture tests.

## `ports/judgement.py`

Must not import:
- TypeSafe;
- Jev SDK;
- OpenAI;
- adapters;
- flashcards;
- hosts.

## `domain/document_index.py`

Must not import provider SDKs/adapters.

## `flashcards/semantic.py`

Must not import:
- provider SDKs;
- concrete adapters;
- state stores;
- capability gateway.

Allowed:
- provider-neutral ports;
- document-index values;
- flashcard planning value types;
- domain validation utilities.

## `hosts/routing.py`

Must not import:
- TypeSafe/Jev SDK;
- concrete Jev adapter;
- concrete OpenAI tutor adapter;
- capability execution internals.

Allowed:
- host contracts;
- `TutorDecisionPort`;
- `ChoiceJudgementPort`;
- `ModelPort`.

## PageIndex adapter

Only provider-specific PageIndex adapter may import PageIndex SDK/types.

## Jev adapter

Only provider-specific Jev adapter may import Jev/TypeSafe SDK/types.

## Unit identity

Architecture/static tests should keep `knowledge/unitizer.py` as the only owner of UnitId creation for normal unitization.

---

# 52. DocumentIndex tests

Create at minimum:

```text
tests/unit/domain/test_document_index.py
tests/unit/knowledge/test_document_index.py
tests/unit/adapters/document_index/test_pageindex.py
```

Test:
- duplicate node key rejection;
- missing parent rejection;
- cycle rejection;
- invalid child mapping;
- invalid locator shapes;
- deterministic fingerprint;
- provider normalization;
- provider objects do not escape;
- PageIndex rebuild with new node IDs can still ground to the same immutable source locator where applicable.

---

# 53. Locator reconciliation tests

Cover:
- one-page PDF node;
- multi-page PDF range;
- last-page range;
- invalid/nonexistent page;
- Markdown line range;
- line range out of bounds;
- text offset range;
- wrong source/revision binding;
- resolved span bounds;
- quoted snippet verification if supported.

No ambiguous locator may be silently converted into trusted evidence.

---

# 54. Unitizer migration tests

Test:

```text
DocumentIndex
→ identity-free UnitDraft
→ existing UnitId owner
→ RetrievableUnit
```

Assert that the PageIndex adapter itself never creates UnitId.

Shadow comparison should measure:
- source coverage;
- gaps;
- overlap;
- structural path quality;
- canonical span validity;
- retrieval behavior.

If new unitization changes identity:
- use a new unitizer version;
- preserve historical codecs and resolution;
- never rewrite old events.

---

# 55. Flashcard semantic unit tests

Create:

```text
tests/unit/flashcards/test_semantic.py
```

Use small readable fixtures.

## Pedagogical granularity

PageIndex:

```text
Carbohydrate metabolism
  Glycolysis
    Preparatory phase
      Hexokinase
```

Expected pedagogical topic anchor:

```text
Glycolysis
```

The full structural path remains available as provenance/navigation metadata.

## Stable identity

Same:
- source/revision;
- DocumentIndex;
- semantic policy;
- scripted judgement outputs;

must produce:
- same topic identities;
- same analysis fingerprint.

## Invalidation

Changing any of:
- source revision;
- DocumentIndex fingerprint;
- semantic policy version;
- judgement model identity where policy requires it;

must invalidate the semantic analysis cache key/fingerprint.

---

# 56. Fundamental flashcard gate test

Build one semantic topic with:

```text
p1 CORE
p2 CONTEXT_ONLY
p3 SUPPORTING
p4 EXCLUDED
```

Expected:

```text
SemanticLessonAnalysis:
p1 p2 p3 p4
```

Expected generation projection:

```text
LessonGenerationUnit:
p1 p3
```

Then run the real planner.

Assert:
- no evidence slot references `p2`;
- no evidence slot references `p4`.

Then run planned-scope preparation.

Assert:
- no worker-accessible evidence can resolve `p2`;
- no worker-accessible evidence can resolve `p4`.

This is the real acceptance/security test.

Checking only labels is insufficient.

---

# 57. Flashcard fail-open tests

Simulate:
- Jev timeout;
- malformed response;
- missing probability;
- low margin;
- adapter unavailable.

Expected:
- candidate is preserved;
- fallback reason is recorded;
- no provider failure becomes `EXCLUDED`.

---

# 58. Flashcard regression tests

With semantic preprocessing OFF:

Existing fixtures must produce the same behavior as before.

Prefer:
- same lesson generation input;
- same bundles;
- same slots;
- same policy behavior;
- same serialized plan where the path is unchanged.

Existing lesson-planning tests should not be rewritten merely to accommodate Jev.

---

# 59. Topic segmentation / pedagogical-granularity evals

Because the final design uses PageIndex structure plus semantic anchor selection rather than a second sequential topic tree, evaluate:

- correct pedagogical anchor accuracy;
- over-granularity rate;
- under-granularity rate;
- adjacent candidate same-topic consistency where useful;
- topic-count ratio vs human gold;
- structural path agreement;
- source coverage.

If local merge/split logic is added later, then also measure boundary precision/recall and recovery/drift.

Do not add a sequential boundary algorithm merely because those metrics existed in the earlier proposal.

---

# 60. Cardability eval

Primary objective:

> Reduce generative work without losing useful study concepts.

Measure:

## Card-worthy recall
Among candidates that a strong human/generator would consider worth at least one valid card, how many remain?

This is the primary metric.

## Drop precision
Among dropped candidates, how many were truly non-card-worthy?

## False exclusion
Track separately, especially for CORE.

## Generator input reduction
Tokens/characters avoided.

## Generation calls/bundles saved

## Cost per lesson

## Final concept coverage

## Card quality/acceptance

A cheap classifier that halves token cost but loses important concepts is a regression.

---

# 61. Flashcard end-to-end comparison

Compare at minimum:

1. legacy/current baseline before removal;
2. PageIndex navigation/candidate generation without Jev;
3. PageIndex + Jev semantic classification.

Optional diagnostic ablation:
4. Jev classification on legacy candidates during shadow only, if useful to isolate effects.

Measure:
- total wall time;
- one-time PageIndex preprocessing cost;
- Jev time;
- generator time;
- generator tokens;
- total cost;
- card count;
- accepted-card count;
- coverage;
- duplication;
- topic coherence.

Always report initial index cost separately from subsequent generation cost.

---

# 62. Tutor routing tests

Create:

```text
tests/unit/hosts/test_routing.py
```

Use scripted/fake:
- `ChoiceJudgementPort`;
- `ModelPort`;
- legacy `TutorDecisionPort`.

Test:

## Allowed routes only
Jev never receives illegal routes.

## Capability choices
Jev receives only advertised capability IDs.

## Unknown selected option
Reject/fallback.

## Empty-input capability
No generative `ModelPort` call.

## Capability binding
After Jev chooses capability A, `ModelPort` sees:
- A's input schema;
- not B/C schemas;
- not full TutorDecision schema.

## Closed continuation
Can complete without the generative model.

## Complex continuation
Jev chooses `ANSWER_DIALOGUE`; model generates only response payload.

## Assistant message
Jev selects route; one narrow generation call.

## Jev failure
Legacy fallback exactly once.

## Validation failure
Fallback exactly once.

## Shadow
Candidate new decision is recorded but legacy decision is returned.

---

# 63. Tutor router eval

Build a labeled dataset of representative tutor turns.

Measure:
- top-1 route accuracy;
- confusion matrix by route;
- false semantic route rate;
- routing coverage if any structural short-circuits exist;
- Jev p50/p95 latency;
- narrow generation p50/p95 latency;
- total end-to-end latency;
- cost per turn;
- provider errors/malformed outputs;
- validation failures;
- emergency fallback rate;
- downstream task success;
- full-model routing calls avoided.

Precision matters more than aggressive coverage during rollout.

---

# 64. Threshold policy — tutor

Thresholds remain Harness policy, not adapter logic.

Separate policy may be used for:
- closed Boolean dialogue;
- closed enum dialogue;
- capability route selection;
- general route selection.

A binary yes/no judgement and a six-capability routing judgement need not use the same acceptance rule.

Threshold configuration participates in routing policy identity/telemetry.

---

# 65. Privacy and payload minimization

Choice judgement requests must receive only the minimum bounded state needed.

## Flashcards
Send:
- candidate canonical text or bounded excerpt;
- local structure/path;
- descriptor;
- selected/local topic anchor information.

Do not serialize the whole source by convenience.

## Tutor
Send:
- latest learner utterance;
- bounded legal routes/options;
- compact capability descriptions;
- closed dialogue choices when applicable.

Do not serialize the entire tutor domain object.

---

# 66. Observability

Every accepted or rejected Jev judgement should be explainable without logging source text by default.

Record:
- use case;
- input fingerprint;
- policy version;
- model ID;
- options;
- chosen option;
- probability distribution;
- provider confidence;
- top-1/top-2 margin;
- accepted/fallback;
- latency;
- usage;
- provider error/fallback reason.

## Flashcard-specific telemetry
- candidate/node key;
- resolved source span identity;
- topic anchor;
- cardability label.

## Tutor-specific telemetry
- route;
- selected capability ID if any;
- legacy route in shadow;
- narrow payload generation used/not used.

Never log secrets or credentials.

---

# 67. Feature modes

Use three-state feature modes:

```text
OFF
SHADOW
ON
```

Recommended:

```text
document_index_mode
flashcard_semantic_mode
tutor_routing_mode
```

Do not create one feature flag per threshold.

Policy versions own semantic details.

---

# 68. Configuration

The repository config is strict and versioned.

When runtime wiring reaches these providers, introduce separate config concepts:

```text
DocumentIndexAdapterConfig
JudgementAdapterConfig
SemanticFeatureConfig
```

Provider credentials remain environment variable references.

Do not put API keys/tokens in persisted repository config.

A config schema change requires an explicit schema-version bump and compatibility tests.

---

# 69. Optional dependency management

Maintain the current property that core runtime does not require provider SDKs.

Jev and PageIndex must be optional extras.

No imports of those SDKs from:
- domain;
- knowledge core;
- flashcards;
- hosts;
- provider-neutral ports.

---

# 70. Retry semantics

Avoid retries at multiple semantic layers.

## PageIndex adapter
Owns transport/provider retry.

## Jev adapter
Owns transport/provider retry.

## Semantic analyzer
Chooses semantic fallback/preservation; does not repeat the same judgement blindly many times.

## Tutor routing
Falls back to legacy/emergency path when required.

Offline document analysis and interactive tutor routing may use different timeout settings over the same low-level port.

---

# 71. ADR

Add a new ADR.

Suggested title:

```text
Bounded semantic judgement and shared derived document indexing
```

It should freeze these decisions:

1. semantic judgements are derived and non-authoritative;
2. Jev transport sits behind generic `ChoiceJudgementPort`;
3. PageIndex is the primary derived document structure;
4. PageIndex does not replace canonical source/revision authority;
5. document-index node IDs are non-canonical;
6. flashcard semantic filtering occurs before deterministic planning;
7. the planner/worker remain provider-agnostic;
8. Jev is the single semantic tutor router;
9. payload generation happens after route selection;
10. `TutorHostRunner` and `validate_decision()` remain the lifecycle/authority boundary;
11. legacy DocumentTree survives only as migration/compatibility code with a removal plan.

Do not rewrite ADR-0010 merely to describe this new layer.

---

# 72. Anti-patterns to reject in review

Reject a PR that:

### Provider leakage
Imports TypeSafe/Jev inside `flashcards/` or `hosts/`.

### PageIndex leakage
Imports PageIndex inside canonical domain or legacy tree code.

### Canonical contamination
Stores PageIndex node IDs in canonical source/unit identity.

### Evidence violation
Uses PageIndex summary as source evidence.

### Second semantic document tree
Maintains permanent Cardine and PageIndex semantic hierarchies in parallel.

### Second UnitId owner
Creates final UnitIds outside the existing unitizer authority.

### Async planner contamination
Makes deterministic flashcard planning remote/model-dependent.

### Wrong LessonParagraph enrichment
Adds Jev/PageIndex/confidence fields to `LessonParagraph`.

### Late flashcard filtering
Adds Jev inside the worker after planning.

### Host runner provider branch
Adds `if jev:` logic to `TutorHostRunner`.

### Full rerouting after Jev
Calls Jev to select a route and then asks a large model to emit a full `TutorDecision` again.

### Provider-centric service object
Creates `JevService` with many domain-specific methods.

### Adapter thresholds
Hard-codes semantic confidence thresholds inside the Jev adapter.

### Silent content loss
Treats provider failure as `EXCLUDED`.

### Silent PageIndex legacy fallback
Keeps a second semantic parser as a hidden production fallback after migration.

### Oversized payloads
Serializes whole domain objects to Jev for convenience.

---

# 73. Implementation sequence

Implementation dependency order differs from feature value priority.

## Phase 1 — provider-neutral primitives

Implement:
- `domain/document_index.py`;
- `ports/document_index.py`;
- `ports/judgement.py`;
- architecture/contract tests.

No external providers yet.

## Phase 2 — provider adapters

Implement:
- PageIndex adapter;
- Jev adapter;
- optional dependency extras;
- provider fixture/contract tests.

## Phase 3 — DocumentIndex reconciliation

Implement:
- locator resolution;
- candidate projection;
- unit-draft projection;
- DocumentIndex fingerprinting.

No primary runtime flip yet.

## Phase 4 — DocumentIndex shadow migration

Run:
- legacy DocumentTree path;
- PageIndex/DocumentIndex path;

in parallel for eval.

Do not change historical authority.

## Phase 5 — unitizer refactor

Separate:
- draft construction;
- final unit identity/materialization.

Keep one UnitId owner.

Add DocumentIndex-derived drafts.

## Phase 6 — flashcard semantic analysis shadow

Implement:
- pedagogical topic-anchor selection;
- cardability;
- semantic receipt;
- cache identity;
- generation projection.

Do not filter production generation yet.

## Phase 7 — flashcard semantic gate ON

Project only:
- CORE;
- SUPPORTING.

Run entire existing planning/worker regression suite.

Enable only after card-worthy recall/false-exclusion benchmarks pass.

## Phase 8 — tutor router shadow

Implement `RoutingTutorDecisionPort`.

Compare against current full tutor decisions.

## Phase 9 — closed dialogue route

Enable first because payload is fully bounded.

## Phase 10 — capability route + deterministic binding

Enable:
- capability selection;
- `{}` / default / trusted-context-only inputs.

## Phase 11 — narrow ModelPort payload generation

Add:
- capability argument binding;
- learner-facing message generation;
- complex dialogue response generation;

all after route selection and with route-specific schemas.

## Phase 12 — Jev becomes primary semantic tutor router

Legacy full tutor remains only shadow/emergency/kill-switch.

## Phase 13 — remove legacy semantic document parser from primary runtime

Only after:
- grounding benchmark;
- retrieval benchmark;
- flashcard benchmark;
- replay compatibility tests.

The target is one document index, not dual permanent representations.

---

# 74. Feature value priority

Once foundational dependencies are accounted for, the expected product/ROI priority remains:

1. Jev card-worthiness gate;
2. PageIndex-backed pedagogical structure/candidate selection;
3. Jev closed pending-dialogue routing;
4. Jev capability routing and narrow payload binding.

The card-worthiness gate has the most direct generative-cost reduction.

The document-index migration is nevertheless foundational because the final architecture should not preserve a duplicate semantic parser merely to deliver the gate faster.

---

# 75. File matrix

| File | Action | Purpose |
|---|---|---|
| `domain/document_index.py` | NEW | provider-neutral shared derived document structure |
| `ports/document_index.py` | NEW | document indexing port |
| `adapters/document_index/pageindex.py` | NEW | only PageIndex provider adapter |
| `knowledge/document_index.py` | NEW | deterministic locator/span reconciliation and derived candidates |
| `ports/judgement.py` | NEW | generic bounded Choice contract |
| `adapters/judgement/jev.py` | NEW | only Jev provider adapter |
| `flashcards/semantic.py` | NEW | flashcard semantic policy and projection |
| `hosts/routing.py` | NEW | Jev semantic tutor router + payload assembly |
| `ports/__init__.py` | MODIFY | export new public ports |
| `knowledge/unitizer.py` | REFACTOR | separate structural drafting from UnitId/materialization |
| `knowledge/units.py` | MINOR/MIGRATION | unitizer-version compatibility if needed |
| `domain/tree.py` | LEGACY/DEPRECATE | no longer primary derived document structure |
| `knowledge/tree.py` | LEGACY/DEPRECATE | shadow then remove primary use |
| `repository_config.py` | MODIFY WHEN WIRED | provider config + feature modes |
| `demo/anatomy.py` | MODIFY | tutor composition |
| `examples/reference_tutor_host.py` | MODIFY | reference composition |
| `pyproject.toml` | MODIFY | optional provider extras |
| `flashcards/planning.py` | NO EDIT | deterministic planner |
| `ports/flashcard_planning.py` | NO EDIT | deterministic planning port |
| `lesson_worker_contracts.py` | NO EDIT | downstream trusted worker contracts |
| `lesson_worker_service.py` | NO EDIT | downstream generation |
| `flashcards/scope.py` | NO EDIT | trusted evidence scope |
| `capabilities/hybrid_flashcards.py` | NO EDIT | consumes trusted plan |
| `hosts/runner.py` | NO EDIT | lifecycle/authority host |
| `ports/tutor_host.py` | NO EDIT | existing decision abstraction |
| `hosts/contracts.py` | NO EDIT initially | existing decisions and validation |
| `adapters/host/openai_responses.py` | NO EDIT initially | legacy shadow/emergency full tutor |
| `ports/model.py` | NO EDIT | generic narrow payload generation already supported |

---

# 76. Final architecture

## Canonical knowledge

```text
source bytes
    ↓
immutable SourceRevision / substrate
    │
    │ canonical authority
    ▼
DERIVED DOCUMENT INDEX
PageIndex adapter
    ↓
DocumentIndex
    ↓
deterministic locator reconciliation
    ↓
host-owned spans / candidate projections
```

## Flashcards

```text
DocumentIndex candidates
    ↓
Jev bounded semantic classification
    ↓
SemanticLessonAnalysis
    ↓
CORE + SUPPORTING projection
    ↓
LessonGenerationUnit
    ↓
EXISTING FlashcardPlanningPolicy
    ↓
EXISTING FlashcardLessonPlanner
    ↓
EXISTING FlashcardLessonPlan
    ↓
EXISTING prepared scope
    ↓
EXISTING worker
    ↓
generative model
```

## Tutor

```text
TutorHostRunner
      │
      ▼
RoutingTutorDecisionPort
      │
      ├─ host legal-route narrowing
      │
      ├─ Jev semantic Choice
      │
      ├─ deterministic route payload when possible
      │
      └─ ModelPort narrow payload generation when needed
      │
      ▼
existing TutorDecision
      │
      ▼
existing validate_decision()
      │
      ▼
existing capability / continuation lifecycle
```

---

# 77. Acceptance criteria

The refactor is correctly integrated only when all of the following are true.

1. The Harness still installs and runs core behavior without Jev and PageIndex extras installed.
2. Core/domain/knowledge ports do not import provider SDKs.
3. `SourceRevision` remains canonical truth.
4. PageIndex is the only primary derived document structure after migration.
5. PageIndex node IDs are never canonical identity.
6. PageIndex summaries are never treated as evidence.
7. Every downstream grounded reference can be verified against the immutable source/revision substrate.
8. `knowledge/unitizer.py` remains the single owner of normal UnitId creation.
9. Historical unit/citation resolution remains compatible.
10. The old deterministic document tree is not maintained as a second permanent semantic representation.
11. Jev is accessed only through `ChoiceJudgementPort`.
12. Flashcard and tutor domains share the low-level judgement primitive but no domain-specific Jev service.
13. Jev provider errors do not silently drop study content.
14. CORE and SUPPORTING may reach the flashcard generator.
15. CONTEXT_ONLY and EXCLUDED do not reach the V1 generative flashcard evidence scope.
16. Existing deterministic flashcard planner behavior remains intact.
17. Existing worker/scope contracts remain intact.
18. Existing lesson-planning regression tests continue to pass when semantic preprocessing is OFF.
19. The fundamental gate test proves excluded/context-only evidence cannot be accessed by the worker.
20. Jev becomes the tutor's primary semantic router after successful shadow evaluation.
21. The generative model after routing sees only the route-specific payload/schema it needs.
22. The generative model does not redo the semantic route selection in the normal path.
23. `TutorHostRunner` remains unchanged.
24. Every routed decision still passes through existing `validate_decision()`.
25. Jev never creates trusted fingerprints, action IDs, capability authority, or continuation fingerprints.
26. Full-tutor routing remains available during shadow/emergency rollout but is not a permanent second normal routing path.
27. Derived caches remain operational state rather than canonical event history.
28. Architecture tests prevent provider leakage and duplicate authorities.
29. The legacy document parser is removed from the primary runtime only after PageIndex grounding/retrieval/flashcard benchmarks pass.
30. Benchmarks demonstrate measurable end-to-end improvement in cost and/or latency without unacceptable loss of study-content recall, grounding quality, or tutor task success.

---

# 78. Final review rule

The correct implementation is not the one with the smallest diff.

It is the one with the smallest **permanent architecture**.

It is acceptable to refactor the current tree/unitizer seam once in order to remove permanent duplicate document representations.

It is not acceptable to:
- rewrite the deterministic planner;
- rewrite the flashcard worker;
- add provider logic to the host runner;
- duplicate UnitId authority;
- keep PageIndex and a separate Cardine semantic parser forever;
- let Jev and a large tutor model both independently route the same turn.

The final system should have:

```text
ONE canonical source/revision substrate
ONE primary derived document index
ONE bounded semantic judgement primitive
ONE semantic tutor router
ONE UnitId authority
ONE host validation/lifecycle authority
```

Provider implementations may change behind their ports without changing those ownership boundaries.
