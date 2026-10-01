"""Discardable pedagogical analysis over verified document-index candidates.

This layer ends at LessonGenerationUnit. Neither the planner nor its workers
know about judgements, provider metadata, or excluded source material.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256

from study_agent.domain._validation import JsonObject, require_text
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.features import FeatureMode
from study_agent.knowledge.document_index import (
    DocumentCandidate,
    DocumentIndexContext,
    candidate_nodes,
)
from study_agent.ports.judgement import (
    ChoiceJudgement,
    ChoiceJudgementPort,
    ChoiceJudgementRequest,
    ChoiceOption,
    validate_judgement,
)
from study_agent.state.serialization import canonical_json_bytes

from .planning import (
    MAX_LESSON_INDEX_ENTRIES,
    CanonicalSourceSpan,
    LessonGenerationUnit,
    LessonParagraph,
    LessonTopic,
    PlanningEligibility,
    PlanningPriority,
)

TOPIC_DEFINITION = (
    "The smallest autonomous conceptual unit representing a recognizable study object; "
    "it may contain phases, components, examples, mechanisms and subordinate details "
    "without changing conceptual identity."
)
CANDIDATE_VERSION = "document-index-partition@1"
ANALYZER_VERSION = "flashcard-semantic@1"


class Cardability(StrEnum):
    CORE = "core"
    SUPPORTING = "supporting"
    CONTEXT_ONLY = "context_only"
    EXCLUDED = "excluded"


_DESCRIPTIONS = {
    Cardability.CORE: "A central study concept that should be actively retrievable.",
    Cardability.SUPPORTING: "Useful secondary knowledge worthy of autonomous recall.",
    Cardability.CONTEXT_ONLY: "Useful explanatory context, not an autonomous recall target.",
    Cardability.EXCLUDED: "Boilerplate, administration, transitions, duplication or noise.",
}


def _digest(namespace: str, value: JsonObject) -> str:
    return sha256(namespace.encode() + b"\0" + canonical_json_bytes(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class SemanticPolicy:
    """Thresholds are supplied by evaluation, never inferred from the provider."""

    policy_id: str
    version: str
    anchor_probability: float
    context_probability: float
    exclusion_probability: float
    minimum_margin: float
    max_excerpt_characters: int = 4096
    max_parallel_candidates: int = 64

    def __post_init__(self) -> None:
        require_text(self.policy_id, "policy_id")
        require_text(self.version, "version")
        for value in (
            self.anchor_probability,
            self.context_probability,
            self.exclusion_probability,
            self.minimum_margin,
        ):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= 1:
                raise ValueError("semantic thresholds must be finite probabilities")
        if self.exclusion_probability < self.context_probability:
            raise ValueError("exclusion must be at least as conservative as context-only")
        if (
            type(self.max_excerpt_characters) is not int
            or not 1 <= self.max_excerpt_characters <= 16000
        ):
            raise ValueError("excerpt bound must be 1..16000 characters")
        if (
            type(self.max_parallel_candidates) is not int
            or not 1 <= self.max_parallel_candidates <= 256
        ):
            raise ValueError("parallel candidate bound must be 1..256")

    @property
    def fingerprint(self) -> str:
        return _digest(
            "semantic-policy@1",
            {
                "id": self.policy_id,
                "version": self.version,
                "topic_definition": TOPIC_DEFINITION,
                "cardability": {label.value: text for label, text in _DESCRIPTIONS.items()},
                "anchor_probability": self.anchor_probability,
                "context_probability": self.context_probability,
                "exclusion_probability": self.exclusion_probability,
                "minimum_margin": self.minimum_margin,
                "max_excerpt_characters": self.max_excerpt_characters,
            },
        )


@dataclass(frozen=True, slots=True)
class SemanticJudgementReceipt:
    use_case: str
    input_fingerprint: str
    judgement: ChoiceJudgement | None
    accepted: bool
    fallback_reason: str | None

    def to_json(self) -> JsonObject:
        value = self.judgement
        return {
            "use_case": self.use_case,
            "input_fingerprint": self.input_fingerprint,
            "accepted": self.accepted,
            "fallback_reason": self.fallback_reason,
            "selected_key": value.selected_key if value else None,
            "probabilities": tuple(
                {"key": item.key, "probability": item.probability} for item in value.probabilities
            )
            if value
            else (),
            "margin": _margin(value) if value else None,
            "confidence": value.confidence if value else None,
            "producer_id": value.producer_id if value else None,
            "producer_version": value.producer_version if value else None,
            "model_id": value.model_id if value else None,
            "latency_ms": value.latency_ms if value else None,
            "usage": {
                key: item
                for key, item in value.usage.items()
                if isinstance(item, (int, float))
                and not isinstance(item, bool)
                and math.isfinite(item)
                and item >= 0
            }
            if value
            else {},
        }


@dataclass(frozen=True, slots=True)
class SemanticCandidate:
    candidate: DocumentCandidate
    anchor_key: str
    cardability: Cardability
    receipts: tuple[SemanticJudgementReceipt, ...]

    def to_json(self) -> JsonObject:
        return {
            "node_key": self.candidate.node_key,
            "span": self.candidate.span.to_json(),
            "anchor_key": self.anchor_key,
            "cardability": self.cardability.value,
            "receipts": tuple(item.to_json() for item in self.receipts),
        }


@dataclass(frozen=True, slots=True)
class SemanticLessonAnalysis:
    lesson_key: str
    index: DocumentIndex
    canonical_input_fingerprint: str
    policy_fingerprint: str
    judgement_identity: str
    candidates: tuple[SemanticCandidate, ...]

    def __post_init__(self) -> None:
        require_text(self.lesson_key, "lesson_key")
        require_text(self.judgement_identity, "judgement_identity")
        object.__setattr__(self, "candidates", tuple(self.candidates))
        for digest in (self.canonical_input_fingerprint, self.policy_fingerprint):
            if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
                raise ValueError("analysis identities must be SHA-256 digests")
        by_key = {node.node_key: node for node in self.index.nodes}
        previous_end = -1
        for item in self.candidates:
            candidate = item.candidate
            if candidate.node_key not in by_key or item.anchor_key not in by_key:
                raise ValueError("semantic candidate/anchor is outside document index")
            if item.anchor_key not in candidate.ancestor_keys:
                raise ValueError("pedagogical anchor must be in the local ancestor path")
            if (
                candidate.span.source_id != self.index.source_id
                or candidate.span.revision_id != self.index.revision_id
                or candidate.span.start_offset < previous_end
            ):
                raise ValueError("semantic candidates must be ordered revision-local spans")
            previous_end = candidate.span.end_offset
            if not isinstance(item.cardability, Cardability):
                raise TypeError("candidate cardability must use the bounded vocabulary")

    @property
    def cache_key(self) -> str:
        return _digest(
            "semantic-cache@1",
            {
                "lesson": self.lesson_key,
                "canonical_input": self.canonical_input_fingerprint,
                "index": self.index.fingerprint,
                "policy": self.policy_fingerprint,
                "judgement_identity": self.judgement_identity,
                "analyzer": ANALYZER_VERSION,
                "candidate_extraction": CANDIDATE_VERSION,
            },
        )

    @property
    def fingerprint(self) -> str:
        return _digest(
            "semantic-analysis@1",
            {
                "cache_key": self.cache_key,
                "candidates": tuple(item.to_json() for item in self.candidates),
            },
        )


def _margin(value: ChoiceJudgement) -> float:
    probabilities = sorted((item.probability for item in value.probabilities), reverse=True)
    return probabilities[0] - probabilities[1]


def _accepted(value: ChoiceJudgement, threshold: float, minimum_margin: float) -> bool:
    probability = next(
        item.probability for item in value.probabilities if item.key == value.selected_key
    )
    return (
        probability >= threshold
        and probability == max(item.probability for item in value.probabilities)
        and _margin(value) >= minimum_margin
    )


class FlashcardSemanticAnalyzer:
    def __init__(
        self,
        judgement: ChoiceJudgementPort,
        policy: SemanticPolicy,
        *,
        judgement_identity: str,
    ) -> None:
        require_text(judgement_identity, "judgement_identity")
        self._judgement = judgement
        self._policy = policy
        # Explicit adapter/model/version identity is required for reusable cache keys.
        self._identity = judgement_identity

    async def analyze(
        self,
        lesson_key: str,
        index: DocumentIndex,
        context: DocumentIndexContext,
        *,
        scope: tuple[CanonicalSourceSpan, ...] | None = None,
    ) -> SemanticLessonAnalysis:
        candidates = candidate_nodes(index, context)
        if scope is not None:
            candidates = _scope_candidates(candidates, scope, index)
        if len(candidates) > MAX_LESSON_INDEX_ENTRIES:
            raise ValueError("semantic_candidate_limit_exceeded")
        nodes = {node.node_key: node for node in index.nodes}
        budget = asyncio.Semaphore(self._policy.max_parallel_candidates)

        async def classify(candidate: DocumentCandidate) -> SemanticCandidate:
            async with budget:
                # Source text comes only from verified immutable normalized bytes.
                text = context.text[candidate.span.start_offset : candidate.span.end_offset]
                excerpt = text[: self._policy.max_excerpt_characters]
                descriptor = (
                    candidate.summary
                    or candidate.title
                    or next(
                        (line.strip() for line in text.splitlines() if line.strip()),
                        "Source passage",
                    )
                )
                state: JsonObject = {
                    "text": excerpt,
                    "excerpt_complete": len(text) == len(excerpt),
                    "descriptor": descriptor[:1000],
                    "path": tuple(title[:256] for title in candidate.document_path[-16:]),
                    "relative_position": candidate.order,
                }
                anchors = candidate.ancestor_keys
                anchor = candidate.node_key
                receipts: list[SemanticJudgementReceipt] = []
                if len(anchors) > 1:
                    request = ChoiceJudgementRequest(
                        "Select the local pedagogical topic anchor. " + TOPIC_DEFINITION,
                        state,
                        tuple(
                            ChoiceOption(key, (nodes[key].title or "Document section")[:1000])
                            for key in anchors
                        ),
                        {"use_case": "topic_anchor", "policy": self._policy.fingerprint},
                    )
                    receipt = await self._judge(request, self._policy.anchor_probability)
                    receipts.append(receipt)
                    if receipt.accepted and receipt.judgement is not None:
                        anchor = receipt.judgement.selected_key
                card_request = ChoiceJudgementRequest(
                    "Classify this canonical passage for flashcard recall. Choose only one label.",
                    {**state, "anchor_title": (nodes[anchor].title or "Document section")[:1000]},
                    tuple(ChoiceOption(label.value, text) for label, text in _DESCRIPTIONS.items()),
                    {"use_case": "cardability", "policy": self._policy.fingerprint},
                )
                receipt = await self._judge(card_request, None)
                label = Cardability.SUPPORTING  # failure preserves source content
                if receipt.judgement is not None:
                    selected = Cardability(receipt.judgement.selected_key)
                    threshold = (
                        self._policy.exclusion_probability
                        if selected is Cardability.EXCLUDED
                        else self._policy.context_probability
                        if selected is Cardability.CONTEXT_ONLY
                        else 0.0
                    )
                    accepted = _accepted(receipt.judgement, threshold, self._policy.minimum_margin)
                    # A bounded excerpt cannot justify dropping unseen study concepts.
                    reason: str | None
                    if selected in (Cardability.EXCLUDED, Cardability.CONTEXT_ONLY) and len(
                        excerpt
                    ) < len(text):
                        accepted = False
                        reason = "incomplete_excerpt"
                    else:
                        reason = None if accepted else "ambiguous_distribution"
                    receipt = SemanticJudgementReceipt(
                        receipt.use_case,
                        receipt.input_fingerprint,
                        receipt.judgement,
                        accepted,
                        reason,
                    )
                    if accepted:
                        label = selected
                receipts.append(receipt)
                return SemanticCandidate(candidate, anchor, label, tuple(receipts))

        analyzed = await asyncio.gather(*(classify(item) for item in candidates))
        return SemanticLessonAnalysis(
            lesson_key,
            index,
            _digest(
                "semantic-canonical-input@1",
                {
                    "source": str(index.source_id),
                    "revision": str(index.revision_id),
                    "substrate": str(index.substrate_id),
                    "normalization": context.substrate.normalization_version,
                    "spans": tuple(item.span.to_json() for item in candidates),
                },
            ),
            self._policy.fingerprint,
            self._identity,
            tuple(analyzed),
        )

    async def _judge(
        self,
        request: ChoiceJudgementRequest,
        threshold: float | None,
    ) -> SemanticJudgementReceipt:
        use_case = str(request.metadata["use_case"])
        fingerprint = _digest(
            "semantic-judgement-input@1",
            {
                "instruction": request.instruction,
                "state": request.state,
                "options": tuple(
                    {"key": option.key, "description": option.description}
                    for option in request.options
                ),
                "metadata": request.metadata,
            },
        )
        try:
            result = await self._judgement.judge(request)
            validate_judgement(request, result)
            identity = f"{result.producer_id}@{result.producer_version}/{result.model_id}"
            if identity != self._identity:
                raise ValueError("judgement_identity_mismatch")
        except Exception:
            # Never retain raw provider exceptions, source text, or credentials.
            return SemanticJudgementReceipt(
                use_case, fingerprint, None, False, "judgement_unavailable"
            )
        accepted = threshold is None or _accepted(result, threshold, self._policy.minimum_margin)
        return SemanticJudgementReceipt(
            use_case,
            fingerprint,
            result,
            accepted,
            None if accepted else "ambiguous_distribution",
        )


def generation_unit(
    analysis: SemanticLessonAnalysis,
    *,
    title: str,
    context: DocumentIndexContext,
) -> LessonGenerationUnit:
    """Project only recall targets; source order and flat anchor runs are preserved."""
    candidate_nodes(analysis.index, context)  # revalidate immutable source binding
    nodes = {node.node_key: node for node in analysis.index.nodes}
    topics: list[LessonTopic] = []
    paragraphs: list[LessonParagraph] = []
    runs: list[list[SemanticCandidate]] = []
    for item in analysis.candidates:
        if not runs or runs[-1][-1].anchor_key != item.anchor_key:
            runs.append([])
        runs[-1].append(item)
    for run in runs:
        eligible = [
            item for item in run if item.cardability in (Cardability.CORE, Cardability.SUPPORTING)
        ]
        anchor = run[0].anchor_key
        span = CanonicalSourceSpan(
            analysis.index.source_id,
            analysis.index.revision_id,
            run[0].candidate.span.start_offset,
            run[-1].candidate.span.end_offset,
            (
                f"Normalized text [{run[0].candidate.span.start_offset}:"
                f"{run[-1].candidate.span.end_offset}]"
            ),
        )
        topic_key = "topic-" + _digest(
            "semantic-topic@1",
            {
                "lesson": analysis.lesson_key,
                "index": analysis.index.fingerprint,
                "anchor": anchor,
                "span": span.to_json(),
                "policy": analysis.policy_fingerprint,
            },
        )
        paragraph_keys: list[str] = []
        for item in eligible:
            raw = context.text[item.candidate.span.start_offset : item.candidate.span.end_offset]
            if not raw.strip():
                continue
            start = item.candidate.span.start_offset + len(raw) - len(raw.lstrip())
            end = item.candidate.span.end_offset - (len(raw) - len(raw.rstrip()))
            evidence_span = CanonicalSourceSpan(
                analysis.index.source_id,
                analysis.index.revision_id,
                start,
                end,
                f"Normalized text [{start}:{end}]",
            )
            key = "paragraph-" + _digest(
                "semantic-paragraph@1", {"topic": topic_key, "span": item.candidate.span.to_json()}
            )
            paragraph_keys.append(key)
            paragraphs.append(
                LessonParagraph(
                    key,
                    topic_key,
                    len(paragraphs),
                    evidence_span,
                    end - start,
                )
            )
        labels = {item.cardability for item in run}
        eligibility = (
            PlanningEligibility.ELIGIBLE
            if paragraph_keys
            else PlanningEligibility.CONTEXT_ONLY
            if Cardability.CONTEXT_ONLY in labels
            else PlanningEligibility.EXCLUDED
        )
        priority = (
            PlanningPriority.CORE
            if paragraph_keys and Cardability.CORE in labels
            else PlanningPriority.SUPPORTING
            if eligible
            else PlanningPriority.NONE
        )
        topics.append(
            LessonTopic(
                topic_key,
                (nodes[anchor].title or title)[:1000],
                1,
                None,
                len(topics),
                span,
                tuple(paragraph_keys),
                eligibility,
                priority,
            )
        )
    return LessonGenerationUnit(analysis.lesson_key, title, tuple(topics), tuple(paragraphs))


async def preprocess_generation(
    original: LessonGenerationUnit,
    *,
    mode: FeatureMode,
    analyzer: FlashcardSemanticAnalyzer,
    index: DocumentIndex | None = None,
    context: DocumentIndexContext | None = None,
) -> tuple[LessonGenerationUnit, SemanticLessonAnalysis | None]:
    """OFF makes no provider call; SHADOW returns the exact original unit."""
    if not isinstance(mode, FeatureMode):
        raise TypeError("mode must be FeatureMode")
    if mode is FeatureMode.OFF:
        return original, None
    if index is None or context is None:
        raise ValueError("document_index_unavailable")
    analysis = await analyzer.analyze(
        original.unit_key,
        index,
        context,
        scope=tuple(item.span for item in original.paragraphs),
    )
    candidate = generation_unit(analysis, title=original.title, context=context)
    return (original if mode is FeatureMode.SHADOW else candidate), analysis


def _scope_candidates(
    candidates: tuple[DocumentCandidate, ...],
    scope: tuple[CanonicalSourceSpan, ...],
    index: DocumentIndex,
) -> tuple[DocumentCandidate, ...]:
    """A full-source index cannot expand the trusted lesson selection."""
    clipped: list[DocumentCandidate] = []
    previous_end = -1
    for allowed in scope:
        if (
            allowed.source_id != index.source_id
            or allowed.revision_id != index.revision_id
            or allowed.start_offset < previous_end
        ):
            raise ValueError("lesson scope must be ordered within the indexed revision")
        cursor = allowed.start_offset
        for item in candidates:
            start = max(allowed.start_offset, item.span.start_offset)
            end = min(allowed.end_offset, item.span.end_offset)
            if end <= start:
                continue
            if start != cursor:
                raise ValueError("document_index_incomplete_lesson_coverage")
            span = CanonicalSourceSpan(
                index.source_id,
                index.revision_id,
                start,
                end,
                f"Normalized text [{start}:{end}]",
            )
            clipped.append(
                replace(
                    item,
                    span=span,
                    order=len(clipped),
                    candidate_key=f"{item.node_key}:{start}:{end}",
                    summary=item.summary
                    if item.span.start_offset == start and item.span.end_offset == end
                    else None,
                )
            )
            cursor = end
        if cursor != allowed.end_offset:
            raise ValueError("document_index_incomplete_lesson_coverage")
        previous_end = allowed.end_offset
    return tuple(clipped)
