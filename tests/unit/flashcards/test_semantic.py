from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
from typing import cast

import pytest

from study_agent.domain import ChunkId, Citation, SourceChunk
from study_agent.domain._validation import JsonObject
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.features import FeatureMode
from study_agent.flashcards.lesson_worker_contracts import (
    ResolvedPlannedBundleEvidence,
    RevisionContentCommitment,
)
from study_agent.flashcards.planning import (
    CanonicalSourceSpan,
    LessonGenerationUnit,
    LessonParagraph,
    LessonTopic,
    plan_flashcard_lesson,
)
from study_agent.flashcards.semantic import (
    Cardability,
    FlashcardSemanticAnalyzer,
    SemanticPolicy,
    generation_unit,
    preprocess_generation,
)
from study_agent.grounding import EvidenceEnvelope
from study_agent.ports.judgement import (
    ChoiceJudgement,
    ChoiceJudgementRequest,
    ChoiceProbability,
)
from study_agent.ports.retrieval import (
    EvidenceStatus,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    retrieval_read_set_fingerprint,
)
from tests.unit.knowledge.test_document_index import TEXT, context, index, node

# These thresholds are fixture-only; production policy must come from gold data.
POLICY = SemanticPolicy("fixture", "1", 0.8, 0.9, 0.99, 0.2)


class Judge:
    def __init__(self, *, fail: bool = False, ambiguous: bool = False) -> None:
        self.requests: list[ChoiceJudgementRequest] = []
        self.fail = fail
        self.ambiguous = ambiguous

    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
        self.requests.append(request)
        if self.fail:
            raise RuntimeError("provider exception with private source payload")
        state = cast(JsonObject, request.state)
        if request.metadata["use_case"] == "topic_anchor":
            selected = request.options[0].key
        else:
            text = state["text"]
            selected = {"Café\n": "excluded", "Valves\n": "core", "Heart": "context_only"}.get(
                str(text), "supporting"
            )
        probability = 0.51 if self.ambiguous else 1.0
        other = (1 - probability) / (len(request.options) - 1)
        return ChoiceJudgement(
            selected,
            tuple(
                ChoiceProbability(option.key, probability if option.key == selected else other)
                for option in request.options
            ),
            None,
            "fake",
            "1",
            "fixture",
            1.0,
            {},
        )


def analyzer(judge: Judge) -> FlashcardSemanticAnalyzer:
    return FlashcardSemanticAnalyzer(judge, POLICY, judgement_identity="fake@1/fixture")


def derived_index() -> DocumentIndex:
    return index(
        (
            node(children=("valves", "heart")),
            node("valves", parent="root", order=1, start=5, end=12),
            node("heart", parent="root", order=2, start=12, end=len(TEXT)),
        )
    )


def original_unit(*, start: int = 0, end: int = len(TEXT)) -> LessonGenerationUnit:
    binding = context()
    span = CanonicalSourceSpan(
        binding.source.source_id, binding.source.revision_id, start, end, "lesson"
    )
    return LessonGenerationUnit(
        "lesson",
        "Anatomy",
        (LessonTopic("topic", "Anatomy", 1, None, 0, span, ("paragraph",)),),
        (LessonParagraph("paragraph", "topic", 0, span, end - start),),
    )


def test_only_core_and_supporting_reach_existing_planner_slots() -> None:
    judge = Judge()
    analysis = asyncio.run(analyzer(judge).analyze("lesson", derived_index(), context()))
    assert [item.cardability for item in analysis.candidates] == [
        Cardability.EXCLUDED,
        Cardability.CORE,
        Cardability.CONTEXT_ONLY,
    ]
    unit = generation_unit(analysis, title="Anatomy", context=context())
    assert len(analysis.candidates) == 3
    assert [(p.span.start_offset, p.span.end_offset) for p in unit.paragraphs] == [(5, 11)]
    plan = plan_flashcard_lesson(unit)
    assert [(s.span.start_offset, s.span.end_offset) for b in plan.bundles for s in b.slots] == [
        (5, 11)
    ]
    assert all(
        context().text[p.span.start_offset : p.span.end_offset] != "Derived summary"
        for p in unit.paragraphs
    )


def envelope(spans: tuple[CanonicalSourceSpan, ...]) -> EvidenceEnvelope:
    evidence: list[RetrievalEvidence] = []
    for number, span in enumerate(spans):
        text = TEXT[span.start_offset : span.end_offset]
        chunk = SourceChunk(
            ChunkId(f"chunk-{number}"),
            span.source_id,
            span.revision_id,
            span.start_offset,
            span.end_offset,
            (),
            number,
            sha256(text.encode()).hexdigest(),
            "fixture",
        )
        evidence.append(
            RetrievalEvidence(
                chunk,
                Citation(
                    span.source_id,
                    span.revision_id,
                    chunk.chunk_id,
                    span.start_offset,
                    span.end_offset,
                    span.locator,
                    text,
                ),
                text,
                1.0,
            )
        )
    values = tuple(evidence)
    return EvidenceEnvelope.from_retrieval(
        RetrievalEvidenceSet(
            EvidenceStatus.SUFFICIENT,
            values,
            "a" * 64,
            "fixture",
            "1",
            "fixture",
            retrieval_read_set_fingerprint(values),
        )
    )


def test_worker_evidence_validator_rejects_excluded_and_context_only_material() -> None:
    analysis = asyncio.run(analyzer(Judge()).analyze("lesson", derived_index(), context()))
    plan = plan_flashcard_lesson(generation_unit(analysis, title="Anatomy", context=context()))
    bundle = plan.bundles[0]
    commitments = (
        RevisionContentCommitment(context().source.revision_id, sha256(TEXT.encode()).hexdigest()),
    )
    approved = tuple(slot.span for slot in bundle.slots)
    resolved = ResolvedPlannedBundleEvidence(
        envelope(approved), commitments, plan.plan_fingerprint, bundle.bundle_id
    )
    resolved.validate(plan, bundle, commitments)
    assert [item.evidence.text for item in resolved.envelope.items] == ["Valves"]
    for position in (0, 2):
        leaked = analysis.candidates[position].candidate.span
        raw = TEXT[leaked.start_offset : leaked.end_offset]
        leaked = replace(leaked, end_offset=leaked.end_offset - (len(raw) - len(raw.rstrip())))
        # Replacing a slot with dropped text fails even with the same item count.
        with pytest.raises(ValueError, match="planned slot"):
            replace(resolved, envelope=envelope((leaked,))).validate(plan, bundle, commitments)
        # Appending dropped text to the generator's factual envelope also fails.
        with pytest.raises(ValueError, match="one item"):
            replace(resolved, envelope=envelope((*approved, leaked))).validate(
                plan, bundle, commitments
            )


@pytest.mark.parametrize("judge", [Judge(fail=True), Judge(ambiguous=True)])
def test_provider_failure_or_weak_exclusion_preserves_every_candidate(judge: Judge) -> None:
    analysis = asyncio.run(analyzer(judge).analyze("lesson", derived_index(), context()))
    unit = generation_unit(analysis, title="Anatomy", context=context())
    assert len(unit.paragraphs) == len(analysis.candidates) == 3
    assert all(
        item.cardability in (Cardability.CORE, Cardability.SUPPORTING)
        for item in analysis.candidates
    )
    assert "private source" not in str(
        [r.to_json() for c in analysis.candidates for r in c.receipts]
    )


def test_off_and_shadow_preserve_original_plan_bytes() -> None:
    original = original_unit()
    judge = Judge()
    unit, receipt = asyncio.run(
        preprocess_generation(original, mode=FeatureMode.OFF, analyzer=analyzer(judge))
    )
    assert unit is original and receipt is None and judge.requests == []
    unit, receipt = asyncio.run(
        preprocess_generation(
            original,
            mode=FeatureMode.SHADOW,
            analyzer=analyzer(judge),
            index=derived_index(),
            context=context(),
        )
    )
    assert unit is original and receipt is not None
    assert plan_flashcard_lesson(unit).to_bytes() == plan_flashcard_lesson(original).to_bytes()


def test_selected_lesson_cannot_expand_to_whole_document() -> None:
    judge = Judge()
    original = original_unit(start=5, end=12)
    unit, receipt = asyncio.run(
        preprocess_generation(
            original,
            mode=FeatureMode.ON,
            analyzer=analyzer(judge),
            index=derived_index(),
            context=context(),
        )
    )
    assert receipt is not None and len(receipt.candidates) == 1
    assert [(p.span.start_offset, p.span.end_offset) for p in unit.paragraphs] == [(5, 11)]
    assert all(cast(JsonObject, r.state)["text"] == "Valves\n" for r in judge.requests)


def test_provider_identity_and_policy_change_invalidate_analysis() -> None:
    first = asyncio.run(analyzer(Judge()).analyze("lesson", derived_index(), context()))
    changed = replace(first, judgement_identity="fake@2/fixture")
    assert first.cache_key != changed.cache_key
    assert first.fingerprint != changed.fingerprint
    assert POLICY.fingerprint != replace(POLICY, exclusion_probability=1.0).fingerprint
    assert POLICY.fingerprint == replace(POLICY, max_parallel_candidates=16).fingerprint


def test_partial_excerpt_cannot_exclude_unseen_content() -> None:
    judge = Judge()
    short = FlashcardSemanticAnalyzer(
        judge, replace(POLICY, max_excerpt_characters=2), judgement_identity="fake@1/fixture"
    )

    # Force the classifier to choose exclusion even for a truncated excerpt.
    async def excluded(request: ChoiceJudgementRequest) -> ChoiceJudgement:
        if request.metadata["use_case"] == "topic_anchor":
            return await judge.judge(request)
        return ChoiceJudgement(
            "excluded",
            tuple(
                ChoiceProbability(o.key, 1.0 if o.key == "excluded" else 0.0)
                for o in request.options
            ),
            None,
            "fake",
            "1",
            "fixture",
            None,
        )

    judge.judge = excluded  # type: ignore[method-assign]
    analysis = asyncio.run(short.analyze("lesson", index(), context()))
    assert analysis.candidates[0].cardability is Cardability.SUPPORTING
    assert analysis.candidates[0].receipts[-1].fallback_reason == "incomplete_excerpt"


def test_index_failure_is_explicit_in_on_mode() -> None:
    with pytest.raises(ValueError, match="document_index_unavailable"):
        asyncio.run(
            preprocess_generation(original_unit(), mode=FeatureMode.ON, analyzer=analyzer(Judge()))
        )


def test_semantic_cancellation_is_not_content_retention_fallback() -> None:
    class Cancelled(Judge):
        async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(analyzer(Cancelled()).analyze("lesson", index(), context()))


def test_qualified_pageindex_pipeline_uses_only_canonical_text() -> None:
    from cardine.adapters.document_index.pageindex import PageIndexDocumentIndexAdapter
    from study_agent.ports.document_index import DocumentIndexRequest

    binding = context()
    request = DocumentIndexRequest(
        binding.source.source_id,
        binding.source.revision_id,
        binding.substrate.substrate_id,
        "text/markdown",
        TEXT.encode(),
        TEXT,
    )
    derived = asyncio.run(PageIndexDocumentIndexAdapter().build(request))
    analysis = asyncio.run(analyzer(Judge()).analyze("lesson", derived, binding))
    unit = generation_unit(analysis, title="Anatomy", context=binding)
    plan = plan_flashcard_lesson(unit)
    spans = tuple(slot.span for bundle in plan.bundles for slot in bundle.slots)
    assert "".join(TEXT[span.start_offset : span.end_offset] for span in spans) == TEXT
    assert all(span.source_id == binding.source.source_id for span in spans)
    assert envelope(spans).items[0].evidence.text == TEXT


def test_cache_identity_prepares_without_judgement_and_roundtrips_source_free() -> None:
    from study_agent.flashcards.semantic import SemanticLessonAnalysis

    judge = Judge()
    service = analyzer(judge)
    key = service.cache_key_for("lesson", derived_index(), context())
    assert judge.requests == []
    analysis = asyncio.run(service.analyze("lesson", derived_index(), context()))
    assert key == analysis.cache_key
    restored = SemanticLessonAnalysis.from_bytes(analysis.to_bytes())
    assert restored == analysis
    service.validate_cached(restored, "lesson", derived_index(), context())
    assert len(judge.requests) == 5  # Validation never calls a provider.
    assert TEXT.encode() not in analysis.to_bytes()
    assert b'"state"' not in analysis.to_bytes()
    assert b'"text"' not in analysis.to_bytes()


@pytest.mark.parametrize("fail", [True, False])
def test_cache_roundtrip_preserves_fallbacks_and_complete_receipts(fail: bool) -> None:
    from study_agent.flashcards.semantic import SemanticLessonAnalysis

    service = analyzer(Judge(fail=fail, ambiguous=not fail))
    analysis = asyncio.run(service.analyze("lesson", derived_index(), context()))
    restored = SemanticLessonAnalysis.from_json(analysis.to_json())
    assert restored.fingerprint == analysis.fingerprint
    service.validate_cached(restored, "lesson", derived_index(), context())
    assert all(
        item.cardability in {Cardability.CORE, Cardability.SUPPORTING}
        for item in restored.candidates
    )


def test_cached_candidate_span_and_receipt_policy_cannot_authorize_exclusion() -> None:
    service = analyzer(Judge())
    analysis = asyncio.run(service.analyze("lesson", derived_index(), context()))
    candidate = analysis.candidates[1]
    forged = replace(candidate, cardability=Cardability.EXCLUDED)
    with pytest.raises(ValueError, match="cardability policy"):
        service.validate_cached(
            replace(analysis, candidates=(analysis.candidates[0], forged, analysis.candidates[2])),
            "lesson",
            derived_index(),
            context(),
        )
    forged = replace(
        candidate,
        candidate=replace(
            candidate.candidate, span=replace(candidate.candidate.span, start_offset=6)
        ),
    )
    with pytest.raises(ValueError, match="catalog"):
        service.validate_cached(
            replace(analysis, candidates=(analysis.candidates[0], forged, analysis.candidates[2])),
            "lesson",
            derived_index(),
            context(),
        )
    forged = replace(
        candidate,
        receipts=(
            candidate.receipts[0],
            replace(candidate.receipts[1], input_fingerprint="a" * 64),
        ),
    )
    with pytest.raises(ValueError, match="input mismatch"):
        service.validate_cached(
            replace(analysis, candidates=(analysis.candidates[0], forged, analysis.candidates[2])),
            "lesson",
            derived_index(),
            context(),
        )


def test_cache_invalidation_for_lesson_scope_index_policy_and_model() -> None:
    service = analyzer(Judge())
    key = service.cache_key_for("lesson", derived_index(), context())
    assert key != service.cache_key_for("other", derived_index(), context())
    assert key != service.cache_key_for("lesson", index(), context())
    assert key != service.cache_key_for(
        "lesson",
        derived_index(),
        context(),
        scope=(original_unit(start=5, end=12).paragraphs[0].span,),
    )
    changed = FlashcardSemanticAnalyzer(
        Judge(), replace(POLICY, exclusion_probability=1), judgement_identity="fake@1/fixture"
    )
    assert key != changed.cache_key_for("lesson", derived_index(), context())
    changed = FlashcardSemanticAnalyzer(Judge(), POLICY, judgement_identity="fake@2/fixture")
    assert key != changed.cache_key_for("lesson", derived_index(), context())


@pytest.mark.parametrize(
    "tamper", ["unknown", "fingerprint", "cache_key", "schema", "receipt", "margin", "usage"]
)
def test_cache_codec_rejects_malformed_payloads(tamper: str) -> None:
    import json

    from study_agent.flashcards.semantic import SemanticLessonAnalysis

    analysis = asyncio.run(analyzer(Judge()).analyze("lesson", derived_index(), context()))
    raw = json.loads(analysis.to_bytes())
    if tamper == "unknown":
        raw["source_text"] = "private"
    elif tamper in {"fingerprint", "cache_key"}:
        raw[tamper] = "a" * 64
    elif tamper == "schema":
        raw["schema"] = "future"
    elif tamper == "receipt":
        raw["candidates"][0]["receipts"][0]["accepted"] = "yes"
    elif tamper == "margin":
        raw["candidates"][0]["receipts"][0]["margin"] = True
    else:
        raw["candidates"][0]["receipts"][0]["usage"] = {"input_tokens": "private"}
    with pytest.raises(ValueError):
        SemanticLessonAnalysis.from_bytes(json.dumps(raw).encode())


def test_cache_codec_rejects_duplicate_keys_and_nan() -> None:
    from study_agent.flashcards.semantic import SemanticLessonAnalysis

    analysis = asyncio.run(analyzer(Judge()).analyze("lesson", index(), context()))
    raw = analysis.to_bytes().replace(
        b'"lesson_key":"lesson"', b'"lesson_key":"lesson","lesson_key":"other"'
    )
    with pytest.raises(ValueError, match="duplicate"):
        SemanticLessonAnalysis.from_bytes(raw)
    with pytest.raises(ValueError):
        SemanticLessonAnalysis.from_bytes(b'{"data":NaN}')


def canonical_chunk() -> SourceChunk:
    from study_agent.ingestion.identity import chunk_id_for

    binding = context()
    digest = sha256(TEXT.encode()).hexdigest()
    chunk_id = chunk_id_for(
        source_id=binding.source.source_id,
        revision_id=binding.source.revision_id,
        start_offset=0,
        end_offset=len(TEXT),
        checksum_sha256=digest,
        chunker_version="fixture",
    )
    return SourceChunk(
        chunk_id,
        binding.source.source_id,
        binding.source.revision_id,
        0,
        len(TEXT),
        (),
        0,
        digest,
        "fixture",
    )


def test_canonical_chunk_crossing_nodes_is_classified_whole_before_exclusion() -> None:
    judge = Judge()
    service = analyzer(judge)
    chunk = canonical_chunk()
    key = service.cache_key_for("lesson", derived_index(), context(), canonical_chunks=(chunk,))
    assert judge.requests == []
    analysis = asyncio.run(
        service.analyze("lesson", derived_index(), context(), canonical_chunks=(chunk,))
    )
    assert analysis.cache_key == key
    assert len(analysis.candidates) == 1
    assert analysis.candidates[0].candidate.span.start_offset == 0
    assert analysis.candidates[0].candidate.span.end_offset == len(TEXT)
    assert analysis.candidates[0].cardability is Cardability.SUPPORTING
    assert cast(JsonObject, judge.requests[0].state)["text"] == TEXT
    assert key != service.cache_key_for("lesson", derived_index(), context())
    service.validate_cached(
        analysis, "lesson", derived_index(), context(), canonical_chunks=(chunk,)
    )
    unit = generation_unit(analysis, title="Anatomy", context=context())
    assert len(unit.paragraphs) == 1
    assert TEXT[unit.paragraphs[0].span.start_offset : unit.paragraphs[0].span.end_offset] == TEXT


def test_canonical_chunk_scope_cannot_classify_a_slice_then_restore_dropped_content() -> None:
    service = analyzer(Judge())
    with pytest.raises(ValueError, match="canonical_chunk_scope_splits_chunk"):
        service.cache_key_for(
            "lesson",
            derived_index(),
            context(),
            canonical_chunks=(canonical_chunk(),),
            scope=(original_unit(start=5, end=12).paragraphs[0].span,),
        )
