"""Deterministic lesson planning for isolated flashcard workers.

The planner consumes a trusted, already ordered lesson description.  It does
not discover files, infer lesson membership, retrieve evidence, or decide how
many cards should be produced.  Its global index is navigation metadata; only
the paragraph spans attached to a bundle form that bundle's factual allowlist.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Protocol, cast

from study_agent.domain import RevisionId, SourceId
from study_agent.domain._validation import (
    JsonObject,
    JsonValue,
    freeze_json,
    freeze_object,
    require_text,
)
from study_agent.flashcards.scope import MAX_CANONICAL_LOCATOR_CHARACTERS, PreparedFlashcardScope

if TYPE_CHECKING:
    from study_agent.ports.flashcard_planning import FlashcardPlanningPolicy

MAX_LESSON_INDEX_ENTRIES = 256
DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET = 5_000
MAX_PLANNED_EVIDENCE_SLOTS = 24

_PLAN_FINGERPRINT_DOMAIN = b"flashcard-lesson-plan@1\0"
_POLICY_RECEIPT_FINGERPRINT_DOMAIN = b"flashcard-planning-policy-receipt@1\0"
_PREPARED_SCOPE_FINGERPRINT_DOMAIN = b"prepared-planned-flashcard-scope@1\0"
_DEFAULT_POLICY_FINGERPRINT_DOMAIN = b"default-structural-flashcard-policy@1\0"
_PORTABLE_IDENTIFIER = re.compile(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*")


class PlanningEligibility(StrEnum):
    ELIGIBLE = "eligible"
    CONTEXT_ONLY = "context_only"
    EXCLUDED = "excluded"


class PlanningPriority(StrEnum):
    CORE = "core"
    SUPPORTING = "supporting"
    NONE = "none"


class PlannedBundleKind(StrEnum):
    TOPIC_GROUP = "topic_group"
    PARAGRAPH_SPLIT = "paragraph_split"
    OVERSIZED_PARAGRAPH = "oversized_paragraph"


@dataclass(frozen=True, slots=True)
class CanonicalSourceSpan:
    """One immutable source-revision range declared by the host."""

    source_id: SourceId
    revision_id: RevisionId
    start_offset: int
    end_offset: int
    locator: str

    def __post_init__(self) -> None:
        if not isinstance(self.source_id, SourceId):
            raise TypeError("source_id must be a SourceId")
        if not isinstance(self.revision_id, RevisionId):
            raise TypeError("revision_id must be a RevisionId")
        if type(self.start_offset) is not int or self.start_offset < 0:
            raise ValueError("start_offset must be a non-negative integer")
        if type(self.end_offset) is not int or self.end_offset <= self.start_offset:
            raise ValueError("end_offset must be greater than start_offset")
        _bounded_text(self.locator, "locator", MAX_CANONICAL_LOCATOR_CHARACTERS)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "source_id": str(self.source_id),
                "revision_id": str(self.revision_id),
                "start_offset": self.start_offset,
                "end_offset": self.end_offset,
                "locator": self.locator,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> CanonicalSourceSpan:
        _exact(
            value,
            {"source_id", "revision_id", "start_offset", "end_offset", "locator"},
            "canonical source span",
        )
        return cls(
            SourceId(_string(value, "source_id")),
            RevisionId(_string(value, "revision_id")),
            _integer(value, "start_offset"),
            _integer(value, "end_offset"),
            _string(value, "locator"),
        )


@dataclass(frozen=True, slots=True)
class LessonParagraph:
    paragraph_key: str
    topic_key: str
    relative_position: int
    span: CanonicalSourceSpan
    visible_character_count: int

    def __post_init__(self) -> None:
        _opaque_key(self.paragraph_key, "paragraph_key")
        _opaque_key(self.topic_key, "topic_key")
        _non_negative_integer(self.relative_position, "relative_position")
        if not isinstance(self.span, CanonicalSourceSpan):
            raise TypeError("span must be a CanonicalSourceSpan")
        _positive_integer(self.visible_character_count, "visible_character_count")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "paragraph_key": self.paragraph_key,
                "topic_key": self.topic_key,
                "relative_position": self.relative_position,
                "span": self.span.to_json(),
                "visible_character_count": self.visible_character_count,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> LessonParagraph:
        _exact(
            value,
            {
                "paragraph_key",
                "topic_key",
                "relative_position",
                "span",
                "visible_character_count",
            },
            "lesson paragraph",
        )
        return cls(
            _string(value, "paragraph_key"),
            _string(value, "topic_key"),
            _integer(value, "relative_position"),
            CanonicalSourceSpan.from_json(_mapping(value["span"], "paragraph span")),
            _integer(value, "visible_character_count"),
        )


@dataclass(frozen=True, slots=True)
class LessonTopic:
    topic_key: str
    title: str
    heading_level: int
    parent_topic_key: str | None
    relative_position: int
    span: CanonicalSourceSpan
    paragraph_keys: tuple[str, ...] = ()
    declared_eligibility: PlanningEligibility | None = None
    declared_priority: PlanningPriority | None = None

    def __post_init__(self) -> None:
        _opaque_key(self.topic_key, "topic_key")
        _bounded_text(self.title, "title", 1_000)
        if type(self.heading_level) is not int or not 1 <= self.heading_level <= 64:
            raise ValueError("heading_level must be an integer between 1 and 64")
        if self.parent_topic_key is not None:
            _opaque_key(self.parent_topic_key, "parent_topic_key")
            if self.parent_topic_key == self.topic_key:
                raise ValueError("a topic cannot be its own parent")
        _non_negative_integer(self.relative_position, "relative_position")
        if not isinstance(self.span, CanonicalSourceSpan):
            raise TypeError("span must be a CanonicalSourceSpan")
        keys = tuple(self.paragraph_keys)
        for key in keys:
            _opaque_key(key, "paragraph key")
        if len(set(keys)) != len(keys):
            raise ValueError("paragraph_keys must be ordered and unique")
        if self.declared_eligibility is not None and not isinstance(
            self.declared_eligibility, PlanningEligibility
        ):
            raise TypeError("declared_eligibility must be a PlanningEligibility or None")
        if self.declared_priority is not None and not isinstance(
            self.declared_priority, PlanningPriority
        ):
            raise TypeError("declared_priority must be a PlanningPriority or None")
        if (
            self.declared_eligibility
            in (PlanningEligibility.CONTEXT_ONLY, PlanningEligibility.EXCLUDED)
            and self.declared_priority not in (None, PlanningPriority.NONE)
        ):
            raise ValueError("non-eligible declared topics cannot have core/supporting priority")
        if self.declared_eligibility is None and self.declared_priority is not None:
            raise ValueError("declared priority requires declared eligibility")
        if (
            self.declared_eligibility is PlanningEligibility.ELIGIBLE
            and self.declared_priority is PlanningPriority.NONE
        ):
            raise ValueError("eligible declared topics cannot have none priority")
        object.__setattr__(self, "paragraph_keys", keys)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "topic_key": self.topic_key,
                "title": self.title,
                "heading_level": self.heading_level,
                "parent_topic_key": self.parent_topic_key,
                "relative_position": self.relative_position,
                "span": self.span.to_json(),
                "paragraph_keys": self.paragraph_keys,
                "declared_eligibility": (
                    self.declared_eligibility.value
                    if self.declared_eligibility is not None
                    else None
                ),
                "declared_priority": (
                    self.declared_priority.value if self.declared_priority is not None else None
                ),
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> LessonTopic:
        _exact(
            value,
            {
                "topic_key",
                "title",
                "heading_level",
                "parent_topic_key",
                "relative_position",
                "span",
                "paragraph_keys",
                "declared_eligibility",
                "declared_priority",
            },
            "lesson topic",
        )
        parent = _optional_string(value, "parent_topic_key")
        eligibility = _optional_enum(value, "declared_eligibility", PlanningEligibility)
        priority = _optional_enum(value, "declared_priority", PlanningPriority)
        return cls(
            _string(value, "topic_key"),
            _string(value, "title"),
            _integer(value, "heading_level"),
            parent,
            _integer(value, "relative_position"),
            CanonicalSourceSpan.from_json(_mapping(value["span"], "topic span")),
            _strings(value, "paragraph_keys"),
            eligibility,
            priority,
        )


@dataclass(frozen=True, slots=True)
class LessonGenerationUnit:
    unit_key: str
    title: str
    topics: tuple[LessonTopic, ...]
    paragraphs: tuple[LessonParagraph, ...]

    def __post_init__(self) -> None:
        _opaque_key(self.unit_key, "unit_key")
        _bounded_text(self.title, "title", 1_000)
        topics = tuple(self.topics)
        paragraphs = tuple(self.paragraphs)
        if len(topics) > MAX_LESSON_INDEX_ENTRIES:
            raise ValueError("lesson_index_limit_exceeded")
        if any(not isinstance(topic, LessonTopic) for topic in topics):
            raise TypeError("topics must contain only LessonTopic values")
        if any(not isinstance(paragraph, LessonParagraph) for paragraph in paragraphs):
            raise TypeError("paragraphs must contain only LessonParagraph values")
        _validate_lesson_structure(topics, paragraphs)
        object.__setattr__(self, "topics", topics)
        object.__setattr__(self, "paragraphs", paragraphs)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "unit_key": self.unit_key,
                "title": self.title,
                "topics": tuple(topic.to_json() for topic in self.topics),
                "paragraphs": tuple(paragraph.to_json() for paragraph in self.paragraphs),
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> LessonGenerationUnit:
        _exact(value, {"unit_key", "title", "topics", "paragraphs"}, "lesson unit")
        return cls(
            _string(value, "unit_key"),
            _string(value, "title"),
            tuple(
                LessonTopic.from_json(_mapping(item, "lesson topic"))
                for item in _array(value, "topics")
            ),
            tuple(
                LessonParagraph.from_json(_mapping(item, "lesson paragraph"))
                for item in _array(value, "paragraphs")
            ),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> LessonGenerationUnit:
        return _decode_exact(data, cls.from_json, "lesson unit")


@dataclass(frozen=True, slots=True)
class TopicPlanningClassification:
    topic_key: str
    eligibility: PlanningEligibility
    priority: PlanningPriority

    def __post_init__(self) -> None:
        _opaque_key(self.topic_key, "topic_key")
        if not isinstance(self.eligibility, PlanningEligibility):
            raise TypeError("eligibility must be a PlanningEligibility")
        if not isinstance(self.priority, PlanningPriority):
            raise TypeError("priority must be a PlanningPriority")
        if self.eligibility is PlanningEligibility.ELIGIBLE:
            if self.priority is PlanningPriority.NONE:
                raise ValueError("eligible topics require core or supporting priority")
        elif self.priority is not PlanningPriority.NONE:
            raise ValueError("non-eligible topics require none priority")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "topic_key": self.topic_key,
                "eligibility": self.eligibility.value,
                "priority": self.priority.value,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> TopicPlanningClassification:
        _exact(value, {"topic_key", "eligibility", "priority"}, "topic classification")
        return cls(
            _string(value, "topic_key"),
            PlanningEligibility(_string(value, "eligibility")),
            PlanningPriority(_string(value, "priority")),
        )


@dataclass(frozen=True, slots=True)
class FlashcardPlanningPolicyReceipt:
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    classifications: tuple[TopicPlanningClassification, ...]
    receipt_fingerprint: str

    def __post_init__(self) -> None:
        _portable_identifier(self.policy_id, "policy_id")
        _portable_version(self.policy_version, "policy_version")
        _require_sha256(self.policy_fingerprint, "policy_fingerprint")
        classifications = tuple(self.classifications)
        if any(
            not isinstance(item, TopicPlanningClassification) for item in classifications
        ):
            raise TypeError("classifications must contain TopicPlanningClassification values")
        keys = tuple(item.topic_key for item in classifications)
        if len(set(keys)) != len(keys):
            raise ValueError("policy classifications must have unique topic keys")
        _require_sha256(self.receipt_fingerprint, "receipt_fingerprint")
        if self.receipt_fingerprint != _policy_receipt_fingerprint(
            self.policy_id,
            self.policy_version,
            self.policy_fingerprint,
            classifications,
        ):
            raise ValueError("receipt_fingerprint does not match the policy result")
        object.__setattr__(self, "classifications", classifications)

    @classmethod
    def issue(
        cls,
        policy_id: str,
        policy_version: str,
        policy_fingerprint: str,
        classifications: tuple[TopicPlanningClassification, ...],
    ) -> FlashcardPlanningPolicyReceipt:
        result = tuple(classifications)
        return cls(
            policy_id,
            policy_version,
            policy_fingerprint,
            result,
            _policy_receipt_fingerprint(
                policy_id, policy_version, policy_fingerprint, result
            ),
        )

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "policy_id": self.policy_id,
                "policy_version": self.policy_version,
                "policy_fingerprint": self.policy_fingerprint,
                "classifications": tuple(item.to_json() for item in self.classifications),
                "receipt_fingerprint": self.receipt_fingerprint,
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> FlashcardPlanningPolicyReceipt:
        _exact(
            value,
            {
                "policy_id",
                "policy_version",
                "policy_fingerprint",
                "classifications",
                "receipt_fingerprint",
            },
            "planning policy receipt",
        )
        return cls(
            _string(value, "policy_id"),
            _string(value, "policy_version"),
            _string(value, "policy_fingerprint"),
            tuple(
                TopicPlanningClassification.from_json(_mapping(item, "classification"))
                for item in _array(value, "classifications")
            ),
            _string(value, "receipt_fingerprint"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> FlashcardPlanningPolicyReceipt:
        return _decode_exact(data, cls.from_json, "planning policy receipt")


@dataclass(frozen=True, slots=True)
class LessonTopicIndexEntry:
    topic_key: str
    title: str
    heading_level: int
    parent_topic_key: str | None
    relative_position: int
    span: CanonicalSourceSpan
    direct_visible_character_count: int
    subtree_visible_character_count: int
    eligibility: PlanningEligibility
    priority: PlanningPriority

    def __post_init__(self) -> None:
        _opaque_key(self.topic_key, "topic_key")
        _bounded_text(self.title, "title", 1_000)
        if type(self.heading_level) is not int or not 1 <= self.heading_level <= 64:
            raise ValueError("heading_level must be an integer between 1 and 64")
        if self.parent_topic_key is not None:
            _opaque_key(self.parent_topic_key, "parent_topic_key")
        _non_negative_integer(self.relative_position, "relative_position")
        if not isinstance(self.span, CanonicalSourceSpan):
            raise TypeError("span must be a CanonicalSourceSpan")
        _positive_integer(self.direct_visible_character_count, "direct_visible_character_count")
        _positive_integer(
            self.subtree_visible_character_count, "subtree_visible_character_count"
        )
        if self.subtree_visible_character_count < self.direct_visible_character_count:
            raise ValueError("subtree visible size cannot be smaller than direct size")
        TopicPlanningClassification(self.topic_key, self.eligibility, self.priority)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "topic_key": self.topic_key,
                "title": self.title,
                "heading_level": self.heading_level,
                "parent_topic_key": self.parent_topic_key,
                "relative_position": self.relative_position,
                "span": self.span.to_json(),
                "direct_visible_character_count": self.direct_visible_character_count,
                "subtree_visible_character_count": self.subtree_visible_character_count,
                "eligibility": self.eligibility.value,
                "priority": self.priority.value,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> LessonTopicIndexEntry:
        _exact(
            value,
            {
                "topic_key",
                "title",
                "heading_level",
                "parent_topic_key",
                "relative_position",
                "span",
                "direct_visible_character_count",
                "subtree_visible_character_count",
                "eligibility",
                "priority",
            },
            "lesson topic index entry",
        )
        return cls(
            _string(value, "topic_key"),
            _string(value, "title"),
            _integer(value, "heading_level"),
            _optional_string(value, "parent_topic_key"),
            _integer(value, "relative_position"),
            CanonicalSourceSpan.from_json(_mapping(value["span"], "topic span")),
            _integer(value, "direct_visible_character_count"),
            _integer(value, "subtree_visible_character_count"),
            PlanningEligibility(_string(value, "eligibility")),
            PlanningPriority(_string(value, "priority")),
        )


@dataclass(frozen=True, slots=True)
class PlannedEvidenceSlot:
    slot_key: str
    topic_key: str
    paragraph_keys: tuple[str, ...]
    span: CanonicalSourceSpan
    visible_character_count: int

    def __post_init__(self) -> None:
        _opaque_key(self.slot_key, "slot_key")
        _opaque_key(self.topic_key, "topic_key")
        keys = tuple(self.paragraph_keys)
        if not keys:
            raise ValueError("planned evidence slot requires at least one paragraph")
        for key in keys:
            _opaque_key(key, "paragraph key")
        if len(set(keys)) != len(keys):
            raise ValueError("planned slot paragraph keys must be ordered and unique")
        if not isinstance(self.span, CanonicalSourceSpan):
            raise TypeError("span must be a CanonicalSourceSpan")
        _positive_integer(self.visible_character_count, "visible_character_count")
        object.__setattr__(self, "paragraph_keys", keys)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "slot_key": self.slot_key,
                "topic_key": self.topic_key,
                "paragraph_keys": self.paragraph_keys,
                "span": self.span.to_json(),
                "visible_character_count": self.visible_character_count,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> PlannedEvidenceSlot:
        _exact(
            value,
            {"slot_key", "topic_key", "paragraph_keys", "span", "visible_character_count"},
            "planned evidence slot",
        )
        return cls(
            _string(value, "slot_key"),
            _string(value, "topic_key"),
            _strings(value, "paragraph_keys"),
            CanonicalSourceSpan.from_json(_mapping(value["span"], "slot span")),
            _integer(value, "visible_character_count"),
        )


@dataclass(frozen=True, slots=True)
class PlannedFlashcardBundle:
    bundle_id: str
    relative_position: int
    kind: PlannedBundleKind
    active_topic_keys: tuple[str, ...]
    slots: tuple[PlannedEvidenceSlot, ...]
    visible_character_count: int
    soft_limit_exceeded: bool

    def __post_init__(self) -> None:
        _opaque_key(self.bundle_id, "bundle_id")
        _non_negative_integer(self.relative_position, "relative_position")
        if not isinstance(self.kind, PlannedBundleKind):
            raise TypeError("kind must be a PlannedBundleKind")
        topic_keys = tuple(self.active_topic_keys)
        slots = tuple(self.slots)
        if not topic_keys or len(set(topic_keys)) != len(topic_keys):
            raise ValueError("active_topic_keys must be non-empty, ordered, and unique")
        if not 1 <= len(slots) <= MAX_PLANNED_EVIDENCE_SLOTS:
            raise ValueError("planned bundle must contain 1..24 evidence slots")
        if any(not isinstance(slot, PlannedEvidenceSlot) for slot in slots):
            raise TypeError("slots must contain only PlannedEvidenceSlot values")
        if tuple(dict.fromkeys(slot.topic_key for slot in slots)) != topic_keys:
            raise ValueError("active_topic_keys must exactly match canonical slot topics")
        if sum(slot.visible_character_count for slot in slots) != self.visible_character_count:
            raise ValueError("bundle visible size must equal its planned slots")
        expected_exceeded = self.visible_character_count > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET
        if self.soft_limit_exceeded != expected_exceeded:
            raise ValueError("soft_limit_exceeded must truthfully describe the bundle")
        if self.kind is PlannedBundleKind.OVERSIZED_PARAGRAPH and (
            len(slots) != 1 or len(slots[0].paragraph_keys) != 1 or not expected_exceeded
        ):
            raise ValueError("oversized paragraph bundles must contain one oversized paragraph")
        if type(self.soft_limit_exceeded) is not bool:
            raise TypeError("soft_limit_exceeded must be a bool")
        object.__setattr__(self, "active_topic_keys", topic_keys)
        object.__setattr__(self, "slots", slots)

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "bundle_id": self.bundle_id,
                "relative_position": self.relative_position,
                "kind": self.kind.value,
                "active_topic_keys": self.active_topic_keys,
                "slots": tuple(slot.to_json() for slot in self.slots),
                "visible_character_count": self.visible_character_count,
                "soft_limit_exceeded": self.soft_limit_exceeded,
            }
        )

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> PlannedFlashcardBundle:
        _exact(
            value,
            {
                "bundle_id",
                "relative_position",
                "kind",
                "active_topic_keys",
                "slots",
                "visible_character_count",
                "soft_limit_exceeded",
            },
            "planned flashcard bundle",
        )
        return cls(
            _string(value, "bundle_id"),
            _integer(value, "relative_position"),
            PlannedBundleKind(_string(value, "kind")),
            _strings(value, "active_topic_keys"),
            tuple(
                PlannedEvidenceSlot.from_json(_mapping(item, "planned evidence slot"))
                for item in _array(value, "slots")
            ),
            _integer(value, "visible_character_count"),
            _boolean(value, "soft_limit_exceeded"),
        )


@dataclass(frozen=True, slots=True)
class FlashcardLessonPlan:
    unit: LessonGenerationUnit
    index: tuple[LessonTopicIndexEntry, ...]
    bundles: tuple[PlannedFlashcardBundle, ...]
    bundle_visible_character_target: int
    max_evidence_slots_per_bundle: int
    policy_receipt: FlashcardPlanningPolicyReceipt
    plan_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.unit, LessonGenerationUnit):
            raise TypeError("unit must be a LessonGenerationUnit")
        index = tuple(self.index)
        bundles = tuple(self.bundles)
        if len(index) > MAX_LESSON_INDEX_ENTRIES:
            raise ValueError("lesson_index_limit_exceeded")
        if any(not isinstance(item, LessonTopicIndexEntry) for item in index):
            raise TypeError("index must contain LessonTopicIndexEntry values")
        if any(not isinstance(item, PlannedFlashcardBundle) for item in bundles):
            raise TypeError("bundles must contain PlannedFlashcardBundle values")
        if tuple(item.relative_position for item in index) != tuple(range(len(index))):
            raise ValueError("lesson index positions must be contiguous and canonical")
        if tuple(bundle.relative_position for bundle in bundles) != tuple(range(len(bundles))):
            raise ValueError("bundle positions must be contiguous and canonical")
        if self.bundle_visible_character_target != DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET:
            raise ValueError("unsupported bundle visible-character target")
        if self.max_evidence_slots_per_bundle != MAX_PLANNED_EVIDENCE_SLOTS:
            raise ValueError("unsupported planned evidence-slot limit")
        if not isinstance(self.policy_receipt, FlashcardPlanningPolicyReceipt):
            raise TypeError("policy_receipt must be a FlashcardPlanningPolicyReceipt")
        _validate_complete_plan(self.unit, index, bundles, self.policy_receipt)
        _require_sha256(self.plan_fingerprint, "plan_fingerprint")
        if self.plan_fingerprint != _plan_fingerprint(
            self.unit,
            index,
            bundles,
            self.bundle_visible_character_target,
            self.max_evidence_slots_per_bundle,
            self.policy_receipt,
        ):
            raise ValueError("plan_fingerprint does not match the lesson plan")
        object.__setattr__(self, "index", index)
        object.__setattr__(self, "bundles", bundles)

    @classmethod
    def issue(
        cls,
        unit: LessonGenerationUnit,
        index: tuple[LessonTopicIndexEntry, ...],
        bundles: tuple[PlannedFlashcardBundle, ...],
        policy_receipt: FlashcardPlanningPolicyReceipt,
    ) -> FlashcardLessonPlan:
        canonical_index = tuple(index)
        canonical_bundles = tuple(bundles)
        return cls(
            unit,
            canonical_index,
            canonical_bundles,
            DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET,
            MAX_PLANNED_EVIDENCE_SLOTS,
            policy_receipt,
            _plan_fingerprint(
                unit,
                canonical_index,
                canonical_bundles,
                DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET,
                MAX_PLANNED_EVIDENCE_SLOTS,
                policy_receipt,
            ),
        )

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "unit": self.unit.to_json(),
                "index": tuple(item.to_json() for item in self.index),
                "bundles": tuple(bundle.to_json() for bundle in self.bundles),
                "bundle_visible_character_target": self.bundle_visible_character_target,
                "max_evidence_slots_per_bundle": self.max_evidence_slots_per_bundle,
                "policy_receipt": self.policy_receipt.to_json(),
                "plan_fingerprint": self.plan_fingerprint,
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> FlashcardLessonPlan:
        _exact(
            value,
            {
                "unit",
                "index",
                "bundles",
                "bundle_visible_character_target",
                "max_evidence_slots_per_bundle",
                "policy_receipt",
                "plan_fingerprint",
            },
            "flashcard lesson plan",
        )
        return cls(
            LessonGenerationUnit.from_json(_mapping(value["unit"], "lesson unit")),
            tuple(
                LessonTopicIndexEntry.from_json(_mapping(item, "index entry"))
                for item in _array(value, "index")
            ),
            tuple(
                PlannedFlashcardBundle.from_json(_mapping(item, "bundle"))
                for item in _array(value, "bundles")
            ),
            _integer(value, "bundle_visible_character_target"),
            _integer(value, "max_evidence_slots_per_bundle"),
            FlashcardPlanningPolicyReceipt.from_json(
                _mapping(value["policy_receipt"], "policy receipt")
            ),
            _string(value, "plan_fingerprint"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> FlashcardLessonPlan:
        return _decode_exact(data, cls.from_json, "flashcard lesson plan")


@dataclass(frozen=True, slots=True)
class PreparedPlannedFlashcardScope:
    """One resolved active scope plus immutable lesson-plan commitments."""

    prepared_scope: PreparedFlashcardScope
    plan_fingerprint: str
    bundle_id: str
    bundle_kind: PlannedBundleKind
    active_topic_keys: tuple[str, ...]
    classifications: tuple[TopicPlanningClassification, ...]
    wrapper_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.prepared_scope, PreparedFlashcardScope):
            raise TypeError("prepared_scope must be a PreparedFlashcardScope")
        _require_sha256(self.plan_fingerprint, "plan_fingerprint")
        _opaque_key(self.bundle_id, "bundle_id")
        if not isinstance(self.bundle_kind, PlannedBundleKind):
            raise TypeError("bundle_kind must be a PlannedBundleKind")
        keys = tuple(self.active_topic_keys)
        classifications = tuple(self.classifications)
        if not keys or len(set(keys)) != len(keys):
            raise ValueError("active_topic_keys must be non-empty, ordered, and unique")
        if tuple(item.topic_key for item in classifications) != keys:
            raise ValueError("classifications must exactly match active_topic_keys")
        if any(item.eligibility is not PlanningEligibility.ELIGIBLE for item in classifications):
            raise ValueError("a prepared active scope can contain only eligible topics")
        prepared_keys = tuple(item.topic_key for item in self.prepared_scope.index)
        if tuple(key for key in prepared_keys if key in set(keys)) != keys:
            raise ValueError(
                "active_topic_keys must be an ordered subset of the prepared scope index"
            )
        _require_sha256(self.wrapper_fingerprint, "wrapper_fingerprint")
        expected = _prepared_scope_fingerprint(
            self.prepared_scope,
            self.plan_fingerprint,
            self.bundle_id,
            self.bundle_kind,
            keys,
            classifications,
        )
        if self.wrapper_fingerprint != expected:
            raise ValueError("wrapper_fingerprint does not match the prepared planned scope")
        object.__setattr__(self, "active_topic_keys", keys)
        object.__setattr__(self, "classifications", classifications)

    @classmethod
    def prepare(
        cls,
        prepared_scope: PreparedFlashcardScope,
        plan: FlashcardLessonPlan,
        bundle_id: str,
    ) -> PreparedPlannedFlashcardScope:
        if not isinstance(plan, FlashcardLessonPlan):
            raise TypeError("plan must be a FlashcardLessonPlan")
        bundle = next((item for item in plan.bundles if item.bundle_id == bundle_id), None)
        if bundle is None:
            raise ValueError("bundle_id is not present in the lesson plan")
        by_key = {item.topic_key: item for item in plan.policy_receipt.classifications}
        keys = bundle.active_topic_keys
        result = tuple(by_key[key] for key in keys)
        return cls(
            prepared_scope,
            plan.plan_fingerprint,
            bundle.bundle_id,
            bundle.kind,
            keys,
            result,
            _prepared_scope_fingerprint(
                prepared_scope,
                plan.plan_fingerprint,
                bundle.bundle_id,
                bundle.kind,
                keys,
                result,
            ),
        )

    def validate_against_plan(self, plan: FlashcardLessonPlan) -> None:
        """Reject a decoded wrapper unless it is the exact selected plan bundle."""

        expected = type(self).prepare(self.prepared_scope, plan, self.bundle_id)
        if self != expected:
            raise ValueError("prepared planned scope does not match the lesson plan")

    def to_json(self) -> JsonObject:
        return freeze_object(
            {
                "prepared_scope": self.prepared_scope.to_json(),
                "plan_fingerprint": self.plan_fingerprint,
                "bundle_id": self.bundle_id,
                "bundle_kind": self.bundle_kind.value,
                "active_topic_keys": self.active_topic_keys,
                "classifications": tuple(item.to_json() for item in self.classifications),
                "wrapper_fingerprint": self.wrapper_fingerprint,
            }
        )

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_json(cls, value: Mapping[str, JsonValue]) -> PreparedPlannedFlashcardScope:
        _exact(
            value,
            {
                "prepared_scope",
                "plan_fingerprint",
                "bundle_id",
                "bundle_kind",
                "active_topic_keys",
                "classifications",
                "wrapper_fingerprint",
            },
            "prepared planned flashcard scope",
        )
        return cls(
            PreparedFlashcardScope.from_json(
                _mapping(value["prepared_scope"], "prepared flashcard scope")
            ),
            _string(value, "plan_fingerprint"),
            _string(value, "bundle_id"),
            PlannedBundleKind(_string(value, "bundle_kind")),
            _strings(value, "active_topic_keys"),
            tuple(
                TopicPlanningClassification.from_json(_mapping(item, "classification"))
                for item in _array(value, "classifications")
            ),
            _string(value, "wrapper_fingerprint"),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> PreparedPlannedFlashcardScope:
        return _decode_exact(data, cls.from_json, "prepared planned flashcard scope")


class DefaultStructuralFlashcardPlanningPolicy:
    """Generic policy with no course-, provider-, or model-specific knowledge."""

    policy_id = "default.structural"
    policy_version = "1"
    policy_fingerprint = sha256(
        _DEFAULT_POLICY_FINGERPRINT_DOMAIN
        + b"declared-overrides;source-bearing-eligible;empty-container-context-only"
    ).hexdigest()

    def classify(self, unit: LessonGenerationUnit) -> FlashcardPlanningPolicyReceipt:
        child_keys = {topic.parent_topic_key for topic in unit.topics if topic.parent_topic_key}
        classifications: list[TopicPlanningClassification] = []
        for topic in unit.topics:
            if topic.declared_eligibility is not None:
                eligibility = topic.declared_eligibility
            elif topic.paragraph_keys:
                eligibility = PlanningEligibility.ELIGIBLE
            elif topic.topic_key in child_keys:
                eligibility = PlanningEligibility.CONTEXT_ONLY
            else:
                eligibility = PlanningEligibility.CONTEXT_ONLY

            if eligibility is PlanningEligibility.ELIGIBLE:
                priority = topic.declared_priority or PlanningPriority.SUPPORTING
                if priority is PlanningPriority.NONE:
                    priority = PlanningPriority.SUPPORTING
            else:
                priority = PlanningPriority.NONE
            classifications.append(
                TopicPlanningClassification(topic.topic_key, eligibility, priority)
            )
        return FlashcardPlanningPolicyReceipt.issue(
            self.policy_id,
            self.policy_version,
            self.policy_fingerprint,
            tuple(classifications),
        )


class FlashcardLessonPlanner:
    def __init__(self, policy: FlashcardPlanningPolicy | None = None) -> None:
        self._policy = policy or DefaultStructuralFlashcardPlanningPolicy()

    def plan(self, unit: LessonGenerationUnit) -> FlashcardLessonPlan:
        if not isinstance(unit, LessonGenerationUnit):
            raise TypeError("unit must be a LessonGenerationUnit")
        receipt = self._policy.classify(unit)
        _validate_policy_result(unit, receipt)
        if (
            receipt.policy_id != self._policy.policy_id
            or receipt.policy_version != self._policy.policy_version
            or receipt.policy_fingerprint != self._policy.policy_fingerprint
        ):
            raise ValueError("policy receipt identity must match the invoked planning policy")
        classifications = {item.topic_key: item for item in receipt.classifications}
        descendants = _descendant_keys(unit.topics)
        paragraphs = {item.paragraph_key: item for item in unit.paragraphs}
        index = tuple(
            LessonTopicIndexEntry(
                topic.topic_key,
                topic.title,
                topic.heading_level,
                topic.parent_topic_key,
                topic.relative_position,
                topic.span,
                max(
                    len(topic.title),
                    sum(paragraphs[key].visible_character_count for key in topic.paragraph_keys),
                ),
                sum(
                    max(
                        len(candidate.title),
                        sum(
                            paragraphs[key].visible_character_count
                            for key in candidate.paragraph_keys
                        ),
                    )
                    for candidate in unit.topics
                    if candidate.topic_key in descendants[topic.topic_key]
                ),
                classifications[topic.topic_key].eligibility,
                classifications[topic.topic_key].priority,
            )
            for topic in unit.topics
        )
        bundles = _build_bundles(unit, classifications)
        return FlashcardLessonPlan.issue(unit, index, bundles, receipt)


def plan_flashcard_lesson(
    unit: LessonGenerationUnit, policy: FlashcardPlanningPolicy | None = None
) -> FlashcardLessonPlan:
    return FlashcardLessonPlanner(policy).plan(unit)


def _build_bundles(
    unit: LessonGenerationUnit,
    classifications: Mapping[str, TopicPlanningClassification],
) -> tuple[PlannedFlashcardBundle, ...]:
    active = tuple(
        paragraph
        for paragraph in unit.paragraphs
        if classifications[paragraph.topic_key].eligibility is PlanningEligibility.ELIGIBLE
    )
    if not active:
        return ()

    bundles: list[PlannedFlashcardBundle] = []
    pending: list[LessonParagraph] = []

    def flush(*, split: bool = False) -> None:
        if not pending:
            return
        position = len(bundles)
        slots = tuple(_slot(item) for item in pending)
        visible = sum(item.visible_character_count for item in pending)
        kind = PlannedBundleKind.PARAGRAPH_SPLIT if split else PlannedBundleKind.TOPIC_GROUP
        if len(pending) == 1 and visible > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET:
            kind = PlannedBundleKind.OVERSIZED_PARAGRAPH
        bundles.append(
            PlannedFlashcardBundle(
                f"bundle-{position}",
                position,
                kind,
                tuple(dict.fromkeys(item.topic_key for item in pending)),
                slots,
                visible,
                visible > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET,
            )
        )
        pending.clear()

    by_topic = {
        topic.topic_key: tuple(
            paragraph for paragraph in active if paragraph.topic_key == topic.topic_key
        )
        for topic in unit.topics
    }
    descendants = _descendant_keys(unit.topics)
    eligible = {
        key
        for key, classification in classifications.items()
        if classification.eligibility is PlanningEligibility.ELIGIBLE
    }
    topic_by_key = {topic.topic_key: topic for topic in unit.topics}

    def has_eligible_ancestor(topic: LessonTopic) -> bool:
        parent_key = topic.parent_topic_key
        while parent_key is not None:
            if parent_key in eligible:
                return True
            parent_key = topic_by_key[parent_key].parent_topic_key
        return False

    group_roots = tuple(
        topic
        for topic in unit.topics
        if topic.topic_key in eligible
        and not has_eligible_ancestor(topic)
    )
    groups = tuple(
        tuple(
            paragraph
            for paragraph in active
            if paragraph.topic_key in descendants[root.topic_key]
            and paragraph.topic_key in eligible
        )
        for root in group_roots
    )
    for group in groups:
        group_size = sum(item.visible_character_count for item in group)
        fits_whole = (
            group_size <= DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET
            and len(group) <= MAX_PLANNED_EVIDENCE_SLOTS
        )
        if fits_whole:
            candidate_size = sum(item.visible_character_count for item in pending) + group_size
            if pending and (
                candidate_size > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET
                or len(pending) + len(group) > MAX_PLANNED_EVIDENCE_SLOTS
            ):
                flush()
            pending.extend(group)
            continue

        flush()
        group_topic_keys = tuple(dict.fromkeys(item.topic_key for item in group))
        for topic_key in group_topic_keys:
            topic_paragraphs = by_topic[topic_key]
            topic_size = sum(item.visible_character_count for item in topic_paragraphs)
            topic_fits = (
                topic_size <= DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET
                and len(topic_paragraphs) <= MAX_PLANNED_EVIDENCE_SLOTS
            )
            if topic_fits:
                candidate_size = sum(item.visible_character_count for item in pending) + topic_size
                if pending and (
                    candidate_size > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET
                    or len(pending) + len(topic_paragraphs) > MAX_PLANNED_EVIDENCE_SLOTS
                ):
                    flush(split=True)
                pending.extend(topic_paragraphs)
                continue
            for paragraph in topic_paragraphs:
                if paragraph.visible_character_count > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET:
                    flush(split=True)
                    pending.append(paragraph)
                    flush(split=True)
                    continue
                candidate_size = sum(item.visible_character_count for item in pending)
                if pending and (
                    candidate_size + paragraph.visible_character_count
                    > DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET
                    or len(pending) == MAX_PLANNED_EVIDENCE_SLOTS
                ):
                    flush(split=True)
                pending.append(paragraph)
        flush(split=True)
    flush()
    return tuple(bundles)


def _slot(paragraph: LessonParagraph) -> PlannedEvidenceSlot:
    return PlannedEvidenceSlot(
        f"slot-{paragraph.relative_position}",
        paragraph.topic_key,
        (paragraph.paragraph_key,),
        paragraph.span,
        paragraph.visible_character_count,
    )


def _validate_lesson_structure(
    topics: tuple[LessonTopic, ...], paragraphs: tuple[LessonParagraph, ...]
) -> None:
    if tuple(topic.relative_position for topic in topics) != tuple(range(len(topics))):
        raise ValueError("topic positions must be contiguous and canonical")
    topic_by_key: dict[str, LessonTopic] = {}
    ancestor_stack: list[LessonTopic] = []
    for topic in topics:
        if topic.topic_key in topic_by_key:
            raise ValueError("topic keys must be unique")
        if topic.parent_topic_key is None:
            if topic.heading_level != 1:
                raise ValueError("root topics must have heading_level 1")
        else:
            parent = topic_by_key.get(topic.parent_topic_key)
            if parent is None:
                raise ValueError("topic parents must exist before their children")
            if topic.heading_level != parent.heading_level + 1:
                raise ValueError("child heading level must be exactly one deeper than its parent")
        while ancestor_stack and ancestor_stack[-1].heading_level >= topic.heading_level:
            ancestor_stack.pop()
        if topic.parent_topic_key is not None and (
            not ancestor_stack or ancestor_stack[-1].topic_key != topic.parent_topic_key
        ):
            raise ValueError("topic preorder cannot re-enter a closed subtree")
        topic_by_key[topic.topic_key] = topic
        ancestor_stack.append(topic)
    if tuple(item.relative_position for item in paragraphs) != tuple(range(len(paragraphs))):
        raise ValueError("paragraph positions must be contiguous and canonical")
    paragraph_by_key: dict[str, LessonParagraph] = {}
    previous_by_source: dict[tuple[SourceId, RevisionId], CanonicalSourceSpan] = {}
    for paragraph in paragraphs:
        if paragraph.paragraph_key in paragraph_by_key:
            raise ValueError("paragraph keys must be unique")
        if paragraph.topic_key not in topic_by_key:
            raise ValueError("paragraph owner topic is unknown")
        source = (paragraph.span.source_id, paragraph.span.revision_id)
        previous = previous_by_source.get(source)
        if previous is not None and paragraph.span.start_offset < previous.end_offset:
            raise ValueError("paragraph spans must be ordered and non-overlapping")
        previous_by_source[source] = paragraph.span
        paragraph_by_key[paragraph.paragraph_key] = paragraph
    declared = tuple(key for topic in topics for key in topic.paragraph_keys)
    if len(set(declared)) != len(declared):
        raise ValueError("paragraph ownership cannot be duplicated")
    if set(declared) != set(paragraph_by_key):
        raise ValueError("topic paragraph ownership must be complete")
    if declared != tuple(item.paragraph_key for item in paragraphs):
        raise ValueError("topic paragraph ownership must follow canonical topic order")
    for topic in topics:
        actual = tuple(
            item.paragraph_key for item in paragraphs if item.topic_key == topic.topic_key
        )
        if topic.paragraph_keys != actual:
            raise ValueError("topic paragraph_keys must follow canonical paragraph order")
        for key in topic.paragraph_keys:
            paragraph = paragraph_by_key[key]
            if (
                paragraph.span.source_id != topic.span.source_id
                or paragraph.span.revision_id != topic.span.revision_id
                or paragraph.span.start_offset < topic.span.start_offset
                or paragraph.span.end_offset > topic.span.end_offset
            ):
                raise ValueError("topic span must contain all directly owned paragraphs")


def _validate_policy_result(
    unit: LessonGenerationUnit, receipt: FlashcardPlanningPolicyReceipt
) -> None:
    if not isinstance(receipt, FlashcardPlanningPolicyReceipt):
        raise TypeError("planning policy must return a FlashcardPlanningPolicyReceipt")
    if tuple(item.topic_key for item in receipt.classifications) != tuple(
        topic.topic_key for topic in unit.topics
    ):
        raise ValueError("policy receipt must classify every topic in canonical order")
    declared = {topic.topic_key: topic for topic in unit.topics}
    for classification in receipt.classifications:
        topic = declared[classification.topic_key]
        if (
            topic.declared_eligibility is not None
            and classification.eligibility is not topic.declared_eligibility
        ):
            raise ValueError("policy cannot override trusted declared eligibility")
        if (
            topic.declared_priority is not None
            and classification.priority is not topic.declared_priority
        ):
            raise ValueError("policy cannot override trusted declared priority")


def _validate_complete_plan(
    unit: LessonGenerationUnit,
    index: tuple[LessonTopicIndexEntry, ...],
    bundles: tuple[PlannedFlashcardBundle, ...],
    receipt: FlashcardPlanningPolicyReceipt,
) -> None:
    _validate_policy_result(unit, receipt)
    if tuple(item.topic_key for item in index) != tuple(topic.topic_key for topic in unit.topics):
        raise ValueError("lesson index must exactly match canonical lesson topics")
    classifications = {item.topic_key: item for item in receipt.classifications}
    paragraphs = {item.paragraph_key: item for item in unit.paragraphs}
    descendants = _descendant_keys(unit.topics)
    topics = {item.topic_key: item for item in unit.topics}
    for entry in index:
        classification = classifications[entry.topic_key]
        if (entry.eligibility, entry.priority) != (
            classification.eligibility,
            classification.priority,
        ):
            raise ValueError("lesson index classifications must match the policy receipt")
        topic = topics[entry.topic_key]
        expected_direct = max(
            len(topic.title),
            sum(paragraphs[key].visible_character_count for key in topic.paragraph_keys),
        )
        expected_subtree = sum(
            max(
                len(candidate.title),
                sum(
                    paragraphs[key].visible_character_count
                    for key in candidate.paragraph_keys
                ),
            )
            for candidate in unit.topics
            if candidate.topic_key in descendants[topic.topic_key]
        )
        if (
            entry.title != topic.title
            or entry.heading_level != topic.heading_level
            or entry.parent_topic_key != topic.parent_topic_key
            or entry.relative_position != topic.relative_position
            or entry.span != topic.span
            or entry.direct_visible_character_count != expected_direct
            or entry.subtree_visible_character_count != expected_subtree
        ):
            raise ValueError("lesson index must exactly derive from the trusted lesson unit")
    expected_bundles = _build_bundles(unit, classifications)
    if bundles != expected_bundles:
        raise ValueError("planned bundles must exactly match canonical deterministic bundling")
    paragraph_by_key = {item.paragraph_key: item for item in unit.paragraphs}
    expected = tuple(
        item.paragraph_key
        for item in unit.paragraphs
        if classifications[item.topic_key].eligibility is PlanningEligibility.ELIGIBLE
    )
    actual = tuple(
        key for bundle in bundles for slot in bundle.slots for key in slot.paragraph_keys
    )
    if actual != expected:
        raise ValueError("planned bundle paragraphs must be complete, ordered, and non-overlapping")
    for bundle in bundles:
        for slot in bundle.slots:
            resolved = tuple(paragraph_by_key[key] for key in slot.paragraph_keys)
            if any(item.topic_key != slot.topic_key for item in resolved):
                raise ValueError("a planned slot cannot cross topic ownership")
            if (
                sum(item.visible_character_count for item in resolved)
                != slot.visible_character_count
            ):
                raise ValueError("planned slot visible size does not match its paragraphs")
            first, last = resolved[0], resolved[-1]
            if (
                any(item.span.source_id != first.span.source_id for item in resolved)
                or any(item.span.revision_id != first.span.revision_id for item in resolved)
                or slot.span.source_id != first.span.source_id
                or slot.span.revision_id != first.span.revision_id
                or slot.span.start_offset != first.span.start_offset
                or slot.span.end_offset != last.span.end_offset
            ):
                raise ValueError(
                    "planned slot span must exactly cover contiguous source paragraphs"
                )


def _descendant_keys(topics: tuple[LessonTopic, ...]) -> dict[str, set[str]]:
    result = {topic.topic_key: {topic.topic_key} for topic in topics}
    by_key = {topic.topic_key: topic for topic in topics}
    for topic in reversed(topics):
        parent_key = topic.parent_topic_key
        if parent_key is not None:
            result[parent_key].update(result[topic.topic_key])
    # Accessing the map here also pins that every key is known after structural validation.
    assert all(key in by_key for keys in result.values() for key in keys)
    return result


def _policy_receipt_fingerprint(
    policy_id: str,
    policy_version: str,
    policy_fingerprint: str,
    classifications: tuple[TopicPlanningClassification, ...],
) -> str:
    payload = freeze_object(
        {
            "policy_id": policy_id,
            "policy_version": policy_version,
            "policy_fingerprint": policy_fingerprint,
            "classifications": tuple(item.to_json() for item in classifications),
        }
    )
    return sha256(_POLICY_RECEIPT_FINGERPRINT_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _plan_fingerprint(
    unit: LessonGenerationUnit,
    index: tuple[LessonTopicIndexEntry, ...],
    bundles: tuple[PlannedFlashcardBundle, ...],
    target: int,
    maximum_slots: int,
    receipt: FlashcardPlanningPolicyReceipt,
) -> str:
    payload = freeze_object(
        {
            "unit": unit.to_json(),
            "index": tuple(item.to_json() for item in index),
            "bundles": tuple(item.to_json() for item in bundles),
            "configuration": {
                "bundle_visible_character_target": target,
                "max_evidence_slots_per_bundle": maximum_slots,
            },
            "policy_receipt": receipt.to_json(),
        }
    )
    return sha256(_PLAN_FINGERPRINT_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _prepared_scope_fingerprint(
    prepared_scope: PreparedFlashcardScope,
    plan_fingerprint: str,
    bundle_id: str,
    bundle_kind: PlannedBundleKind,
    active_topic_keys: tuple[str, ...],
    classifications: tuple[TopicPlanningClassification, ...],
) -> str:
    payload = freeze_object(
        {
            "prepared_scope": prepared_scope.to_json(),
            "plan_fingerprint": plan_fingerprint,
            "bundle_id": bundle_id,
            "bundle_kind": bundle_kind.value,
            "active_topic_keys": active_topic_keys,
            "classifications": tuple(item.to_json() for item in classifications),
        }
    )
    return sha256(_PREPARED_SCOPE_FINGERPRINT_DOMAIN + _canonical_bytes(payload)).hexdigest()


def _canonical_bytes(value: JsonObject) -> bytes:
    def plain(item: JsonValue) -> object:
        if isinstance(item, Mapping):
            return {key: plain(child) for key, child in item.items()}
        if isinstance(item, tuple):
            return [plain(child) for child in item]
        return item

    return json.dumps(
        plain(value),
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


class _CanonicalValue(Protocol):
    def to_bytes(self) -> bytes: ...


def _decode_exact[T: _CanonicalValue](
    data: bytes,
    loader: Callable[[Mapping[str, JsonValue]], T],
    name: str,
) -> T:
    decoded: Any = json.loads(data)
    frozen = freeze_json(cast(JsonValue, decoded))
    if not isinstance(frozen, Mapping):
        raise ValueError(f"{name} must be a JSON object")
    value: T = loader(frozen)
    if value.to_bytes() != data:
        raise ValueError(f"{name} bytes are not canonical")
    return value


def _exact(value: Mapping[str, JsonValue], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields must be exactly {sorted(fields)}")


def _mapping(value: JsonValue, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: Mapping[str, JsonValue], key: str) -> tuple[JsonValue, ...]:
    result = value.get(key)
    if not isinstance(result, tuple):
        raise ValueError(f"{key} must be an array")
    return result


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    result = value.get(key)
    if not isinstance(result, str):
        raise ValueError(f"{key} must be a string")
    return result


def _optional_string(value: Mapping[str, JsonValue], key: str) -> str | None:
    result = value.get(key)
    if result is not None and not isinstance(result, str):
        raise ValueError(f"{key} must be a string or null")
    return result


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    result = value.get(key)
    if type(result) is not int:
        raise ValueError(f"{key} must be an integer")
    return result


def _boolean(value: Mapping[str, JsonValue], key: str) -> bool:
    result = value.get(key)
    if type(result) is not bool:
        raise ValueError(f"{key} must be a boolean")
    return result


def _strings(value: Mapping[str, JsonValue], key: str) -> tuple[str, ...]:
    items = _array(value, key)
    if any(not isinstance(item, str) for item in items):
        raise ValueError(f"{key} must contain only strings")
    return cast(tuple[str, ...], items)


def _optional_enum[
    T: (PlanningEligibility, PlanningPriority)
](value: Mapping[str, JsonValue], key: str, enum_type: type[T]) -> T | None:
    item = value.get(key)
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string or null")
    return enum_type(item)


def _bounded_text(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    require_text(value, name)
    if len(value) > maximum:
        raise ValueError(f"{name} exceeds {maximum} characters")


def _opaque_key(value: str, name: str) -> None:
    _bounded_text(value, name, 128)
    lowered = value.lower()
    if "/" in value or "\\" in value or lowered.startswith("sha256:"):
        raise ValueError(f"{name} must be an opaque portable key")


def _portable_identifier(value: str, name: str) -> None:
    _bounded_text(value, name, 128)
    if _PORTABLE_IDENTIFIER.fullmatch(value) is None:
        raise ValueError(f"{name} must be a portable lowercase identifier")


def _portable_version(value: str, name: str) -> None:
    _bounded_text(value, name, 128)
    if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", value) is None:
        raise ValueError(f"{name} must be portable")


def _require_sha256(value: str, name: str) -> None:
    _bounded_text(value, name, 64)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


def _non_negative_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{name} must be a non-negative integer")


def _positive_integer(value: int, name: str) -> None:
    if type(value) is not int or value < 1:
        raise ValueError(f"{name} must be a positive integer")


__all__ = [
    "DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET",
    "MAX_LESSON_INDEX_ENTRIES",
    "MAX_PLANNED_EVIDENCE_SLOTS",
    "CanonicalSourceSpan",
    "DefaultStructuralFlashcardPlanningPolicy",
    "FlashcardLessonPlan",
    "FlashcardLessonPlanner",
    "FlashcardPlanningPolicyReceipt",
    "LessonGenerationUnit",
    "LessonParagraph",
    "LessonTopic",
    "LessonTopicIndexEntry",
    "PlannedBundleKind",
    "PlannedEvidenceSlot",
    "PlannedFlashcardBundle",
    "PlanningEligibility",
    "PlanningPriority",
    "PreparedPlannedFlashcardScope",
    "TopicPlanningClassification",
    "plan_flashcard_lesson",
]
