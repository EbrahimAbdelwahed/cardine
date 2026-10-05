"""Compose derived document structure and bounded judgements over admitted sources.

Canonical chunks remain the evidence and historical identity authority. PageIndex
supplies navigation and anchors; semantic analysis never rechunks source history.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from enum import Enum
from hashlib import sha256
from typing import cast

from cardine.adapters.pageindex import PageIndexCoordinator, PageIndexRevision
from cardine.hosts.routing import TutorRoutingReceipt
from cardine.integrations.study_agent.course_policy import (
    ProjectionConsentView,
    ProviderConsentRequiredError,
)
from study_agent.adapters.sqlite import NamespacedSQLiteRunStore, SQLiteRunStore
from study_agent.domain import Citation, CourseId, SubstrateId
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.features import FeatureMode
from study_agent.domain.substrate import PageMapEntry, Substrate
from study_agent.flashcards.planning import LessonGenerationUnit, LessonParagraph, LessonTopic
from study_agent.flashcards.semantic import (
    Cardability,
    FlashcardSemanticAnalyzer,
    SemanticCandidate,
    SemanticLessonAnalysis,
    generation_unit,
)
from study_agent.knowledge.document_index import DocumentIndexContext, validate_document_index
from study_agent.knowledge.unitizer import candidates_for_canonical_chunks
from study_agent.ports.document_index import DocumentIndexRequest
from study_agent.ports.judgement import ChoiceJudgement, ChoiceJudgementPort, ChoiceJudgementRequest
from study_agent.ports.storage import BlobStore
from study_agent.repository_config import SemanticFeaturesConfig
from study_agent.retrieval import CourseSourceContent, SourceRevisionRecord
from study_agent.state import canonical_json_bytes


def document_context(record: SourceRevisionRecord) -> DocumentIndexContext:
    source = record.source
    provenance = source.conversion_provenance
    pages = () if provenance is None else provenance.page_spans
    substrate = Substrate(
        SubstrateId(f"substrate:sha256:{source.normalized_blob.checksum_sha256}"),
        source.normalized_blob,
        source.normalized_character_length,
        source.normalization_version,
        provenance.page_count if pages and provenance is not None else None,
        tuple(PageMapEntry(page.start_offset, page.page) for page in pages),
    )
    return DocumentIndexContext(source, substrate, record.text)


def document_revision(record: SourceRevisionRecord, blobs: BlobStore) -> PageIndexRevision:
    """Bind indexing to admitted original bytes and the exact normalized substrate."""
    source = record.source
    provenance = source.conversion_provenance
    metadata: JsonObject = {}
    media_type = source.media_type
    if provenance is not None:
        media_type = "application/pdf"
        metadata = {
            "original_sha256": provenance.pdf_sha256,
            "page_count": provenance.page_count,
            "page_map": tuple(
                {"offset": page.start_offset, "page": page.page} for page in provenance.page_spans
            ),
        }
    request = DocumentIndexRequest(
        source.source_id,
        source.revision_id,
        SubstrateId(f"substrate:sha256:{source.normalized_blob.checksum_sha256}"),
        media_type,
        blobs.get(source.blob),
        record.text,
        metadata,
    )
    return PageIndexRevision(
        str(record.course_id),
        str(source.source_id),
        str(source.revision_id),
        record.text,
        source.normalized_blob.checksum_sha256,
        document_index_request=request,
    )


class ConsentChoiceJudgementPort:
    """Apply the existing course provider consent before each Jev operation."""

    def __init__(
        self, delegate: ChoiceJudgementPort, course: CourseId, consent: ProjectionConsentView
    ) -> None:
        self._delegate, self._course, self._consent = delegate, course, consent

    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
        receipt = self._consent.get(self._course)
        if receipt is None or not receipt.granted:
            raise ProviderConsentRequiredError("provider consent is required")
        return await self._delegate.judge(request)


class RoutingReceiptStore:
    """Content-addressed operational telemetry; it cannot append domain events."""

    def __init__(self, runs: SQLiteRunStore) -> None:
        self._store = NamespacedSQLiteRunStore(runs, "tutor-routing-receipts")

    def record(self, receipt: TutorRoutingReceipt) -> None:
        payload = canonical_json_bytes(cast(JsonObject, _derived_json(receipt)))
        self._store.create(sha256(payload).hexdigest(), payload)


def _derived_json(value: object) -> JsonValue:
    if isinstance(value, Enum):
        return cast(JsonValue, value.value)
    if is_dataclass(value) and not isinstance(value, type):
        return {item.name: _derived_json(getattr(value, item.name)) for item in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _derived_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return tuple(_derived_json(item) for item in value)
    if value is None or type(value) in (str, int, float, bool):
        return cast(JsonValue, value)
    raise ValueError("invalid derived receipt value")


class FlashcardSemanticPreprocessor:
    """Process one canonical lesson scope once, before the unchanged planner."""

    def __init__(
        self,
        *,
        content: CourseSourceContent,
        blobs: BlobStore,
        indexes: PageIndexCoordinator,
        runs: SQLiteRunStore,
        features: SemanticFeaturesConfig,
        analyzer: FlashcardSemanticAnalyzer | None,
    ) -> None:
        self._content, self._blobs, self._indexes = content, blobs, indexes
        self._features, self._analyzer = features, analyzer
        self._cache = NamespacedSQLiteRunStore(runs, "flashcard-semantic-analysis")
        # A loop owns its locks; repository compositions can be used by successive loops.
        self._locks: dict[tuple[asyncio.AbstractEventLoop, str], asyncio.Lock] = {}

    async def prepare(
        self, original: LessonGenerationUnit, records: tuple[SourceRevisionRecord, ...]
    ) -> LessonGenerationUnit:
        if self._features.document_index_mode is FeatureMode.OFF:
            return original
        units: list[LessonGenerationUnit] = []
        canonical = {record.source.revision_id: record for record in self._content.catalog()}
        for selected in records:
            record = canonical[selected.source.revision_id]
            context = document_context(record)
            revision = document_revision(record, self._blobs)
            projection = await self._indexes.process_async(revision)
            index = projection.document_index
            if index is None:
                if self._features.document_index_mode is FeatureMode.SHADOW:
                    return original
                raise ValueError(f"document_index_unavailable:{projection.status.value}")
            validate_document_index(index, context)
            canonical_chunks = {chunk.chunk_id: chunk for chunk in record.chunks}
            if any(canonical_chunks.get(chunk.chunk_id) != chunk for chunk in selected.chunks):
                raise ValueError("selected chunks are not canonical")
            lesson_key = original.unit_key + ":" + str(record.source.revision_id)
            if self._features.flashcard_semantic_mode is FeatureMode.OFF:
                candidates = candidates_for_canonical_chunks(
                    index, context, record.chunks, selected_chunks=selected.chunks
                )
                analysis = SemanticLessonAnalysis(
                    lesson_key,
                    index,
                    sha256(
                        canonical_json_bytes(
                            {"spans": tuple(item.span.to_json() for item in candidates)}
                        )
                    ).hexdigest(),
                    sha256(b"pageindex-structural-projection@1").hexdigest(),
                    "deterministic@1/canonical-chunks",
                    tuple(
                        SemanticCandidate(item, item.node_key, Cardability.SUPPORTING, ())
                        for item in candidates
                    ),
                )
            else:
                analysis = await self._analyze(lesson_key, index, context, record, selected)
            projected = generation_unit(analysis, title=record.source.title, context=context)
            # The worker receives exact canonical chunks and canonical locators. No
            # arbitrary index/provider span can pass this full-chunk reconciliation.
            chunks = {(chunk.start_offset, chunk.end_offset): chunk for chunk in selected.chunks}
            paragraphs = []
            for paragraph in projected.paragraphs:
                span = paragraph.span
                chunk = chunks.get((span.start_offset, span.end_offset))
                if chunk is None:
                    raise ValueError("semantic projection is not an admitted canonical chunk")
                citation = self._content.resolve(
                    Citation(
                        chunk.source_id,
                        chunk.revision_id,
                        chunk.chunk_id,
                        span.start_offset,
                        span.end_offset,
                        span.locator,
                        context.text[span.start_offset : span.end_offset],
                    )
                ).citation
                paragraphs.append(replace(paragraph, span=replace(span, locator=citation.locator)))
            units.append(replace(projected, paragraphs=tuple(paragraphs)))
        if self._features.document_index_mode is FeatureMode.SHADOW or (
            self._features.flashcard_semantic_mode is FeatureMode.SHADOW
        ):
            return original
        return _combine_units(original, units)

    async def _analyze(
        self,
        lesson_key: str,
        index: DocumentIndex,
        context: DocumentIndexContext,
        canonical: SourceRevisionRecord,
        selected: SourceRevisionRecord,
    ) -> SemanticLessonAnalysis:
        analyzer = self._analyzer
        if analyzer is None:
            raise ValueError("semantic judgement adapter is unavailable")
        from study_agent.flashcards.planning import CanonicalSourceSpan

        scope = tuple(
            CanonicalSourceSpan(
                chunk.source_id,
                chunk.revision_id,
                chunk.start_offset,
                chunk.end_offset,
                f"unicode:{chunk.start_offset}-{chunk.end_offset}",
            )
            for chunk in selected.chunks
        )
        key = analyzer.cache_key_for(
            lesson_key, index, context, canonical_chunks=canonical.chunks, scope=scope
        )
        lock_key = (asyncio.get_running_loop(), key)
        lock = self._locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            try:
                payload = self._cache.load(key)
            except KeyError:
                payload = None
            if payload is not None:
                analysis = SemanticLessonAnalysis.from_bytes(payload)
                analyzer.validate_cached(
                    analysis,
                    lesson_key,
                    index,
                    context,
                    canonical_chunks=canonical.chunks,
                    scope=scope,
                )
                return analysis
            analysis = await analyzer.analyze(
                lesson_key, index, context, canonical_chunks=canonical.chunks, scope=scope
            )
            # Outages fail open for this request, but do not poison a persistent cache.
            if all(
                receipt.judgement is not None
                for item in analysis.candidates
                for receipt in item.receipts
            ):
                self._cache.create(key, analysis.to_bytes())
            return analysis


def _combine_units(
    original: LessonGenerationUnit, units: list[LessonGenerationUnit]
) -> LessonGenerationUnit:
    topics: list[LessonTopic] = []
    paragraphs: list[LessonParagraph] = []
    for unit in units:
        topic_offset, paragraph_offset = len(topics), len(paragraphs)
        topics.extend(
            replace(item, relative_position=topic_offset + position)
            for position, item in enumerate(unit.topics)
        )
        paragraphs.extend(
            replace(item, relative_position=paragraph_offset + position)
            for position, item in enumerate(unit.paragraphs)
        )
    return LessonGenerationUnit(original.unit_key, original.title, tuple(topics), tuple(paragraphs))


__all__ = [
    "ConsentChoiceJudgementPort",
    "FlashcardSemanticPreprocessor",
    "RoutingReceiptStore",
    "document_context",
    "document_revision",
]
