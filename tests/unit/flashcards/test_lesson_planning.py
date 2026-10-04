from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import replace
from hashlib import sha256
from typing import Any, cast

import pytest

from study_agent.domain import ChunkId, Citation, RevisionId, SourceChunk, SourceId
from study_agent.domain._validation import JsonValue, freeze_json
from study_agent.flashcards import FlashcardScopeIndexEntry, PreparedFlashcardScope
from study_agent.flashcards.planning import (
    DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET,
    MAX_LESSON_INDEX_ENTRIES,
    MAX_PLANNED_EVIDENCE_SLOTS,
    CanonicalSourceSpan,
    FlashcardLessonPlan,
    FlashcardPlanningPolicyReceipt,
    LessonGenerationUnit,
    LessonParagraph,
    LessonTopic,
    PlannedBundleKind,
    PlanningEligibility,
    PlanningPriority,
    PreparedPlannedFlashcardScope,
    TopicPlanningClassification,
    plan_flashcard_lesson,
)
from study_agent.grounding import EvidenceEnvelope
from study_agent.ports import (
    EvidenceStatus,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    retrieval_read_set_fingerprint,
)


def _plain(value: JsonValue) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def test_long_canonical_locators_round_trip_in_plan_and_prepared_scope() -> None:
    locator = "Long heading " * 200 + " · chars 10-20"
    span = _span(10, 20, locator=locator)
    entry = FlashcardScopeIndexEntry("topic", "Bounded heading", locator, 0, 10)
    assert CanonicalSourceSpan.from_json(span.to_json()) == span
    assert FlashcardScopeIndexEntry.from_json(entry.to_json()) == entry
    with pytest.raises(ValueError, match="locator"):
        _span(10, 20, locator="x" * 16_001)
    with pytest.raises(ValueError, match="locator"):
        replace(entry, locator="x" * 16_001)


def _span(
    start: int,
    end: int,
    *,
    source: str = "lesson-source",
    revision: str = "lesson-revision",
    locator: str | None = None,
) -> CanonicalSourceSpan:
    return CanonicalSourceSpan(
        SourceId(source),
        RevisionId(revision),
        start,
        end,
        locator or f"Lesson [{start}:{end}]",
    )


def _paragraph(
    position: int,
    topic_key: str,
    size: int,
    *,
    start: int,
    source: str = "lesson-source",
    revision: str = "lesson-revision",
) -> LessonParagraph:
    return LessonParagraph(
        f"paragraph-{position}",
        topic_key,
        position,
        _span(start, start + size, source=source, revision=revision),
        size,
    )


def _topic(
    key: str,
    position: int,
    *,
    level: int = 1,
    parent: str | None = None,
    paragraph_keys: tuple[str, ...] = (),
    start: int = 0,
    end: int = 20_000,
    source: str = "lesson-source",
    revision: str = "lesson-revision",
    eligibility: PlanningEligibility | None = None,
    priority: PlanningPriority | None = None,
) -> LessonTopic:
    return LessonTopic(
        key,
        key.replace("-", " ").title(),
        level,
        parent,
        position,
        _span(start, end, source=source, revision=revision),
        paragraph_keys,
        eligibility,
        priority,
    )


def _nested_unit() -> LessonGenerationUnit:
    paragraphs = (
        _paragraph(0, "framework", 2_400, start=0),
        _paragraph(1, "framework", 2_600, start=2_400),
        _paragraph(2, "details", 100, start=5_000),
        _paragraph(3, "fragile-fact", 100, start=5_100),
    )
    topics = (
        _topic("lesson-root", 0),
        _topic(
            "framework",
            1,
            level=2,
            parent="lesson-root",
            paragraph_keys=("paragraph-0", "paragraph-1"),
            start=0,
            end=5_000,
        ),
        _topic(
            "details",
            2,
            level=2,
            parent="lesson-root",
            paragraph_keys=("paragraph-2",),
            start=5_000,
            end=5_100,
        ),
        _topic(
            "fragile-fact",
            3,
            level=3,
            parent="details",
            paragraph_keys=("paragraph-3",),
            start=5_100,
            end=5_200,
        ),
    )
    return LessonGenerationUnit("nested-lesson", "Nested lesson", topics, paragraphs)


def _unit_for_sizes(sizes: tuple[int, ...]) -> LessonGenerationUnit:
    starts: list[int] = []
    cursor = 0
    for size in sizes:
        starts.append(cursor)
        cursor += size
    paragraphs = tuple(
        _paragraph(position, "topic", size, start=starts[position])
        for position, size in enumerate(sizes)
    )
    topic = _topic(
        "topic",
        0,
        paragraph_keys=tuple(item.paragraph_key for item in paragraphs),
        end=max(cursor, 1),
    )
    return LessonGenerationUnit("sized-lesson", "Sized lesson", (topic,), paragraphs)


def _prepared_scope(topic_keys: tuple[str, ...]) -> PreparedFlashcardScope:
    text = "Canonical lesson evidence."
    source_id = SourceId("prepared-source")
    revision_id = RevisionId("prepared-revision")
    chunk = SourceChunk(
        ChunkId("prepared-chunk"),
        source_id,
        revision_id,
        0,
        len(text),
        (),
        0,
        sha256(text.encode()).hexdigest(),
        "fixture-chunker-v1",
    )
    evidence = (
        RetrievalEvidence(
            chunk,
            Citation(source_id, revision_id, chunk.chunk_id, 0, len(text), "Lesson", text),
            text,
            1.0,
        ),
    )
    envelope = EvidenceEnvelope.from_retrieval(
        RetrievalEvidenceSet(
            EvidenceStatus.SUFFICIENT,
            evidence,
            "a" * 64,
            "fixture_lexical",
            "1.0.0",
            "fixture-index-v1",
            retrieval_read_set_fingerprint(evidence),
        )
    )
    handle = envelope.items[0].handle
    return PreparedFlashcardScope.prepare(
        tuple(
            FlashcardScopeIndexEntry(
                key,
                key.title(),
                f"Lesson > {key}",
                position,
                100,
                ((handle,) if position == 0 else ()),
            )
            for position, key in enumerate(topic_keys)
        ),
        envelope,
    )


def test_nested_lesson_builds_exact_navigation_index_and_disjoint_bundles() -> None:
    plan = plan_flashcard_lesson(_nested_unit())

    assert tuple(
        (
            item.topic_key,
            item.parent_topic_key,
            item.relative_position,
            item.eligibility,
            item.priority,
            item.direct_visible_character_count,
            item.subtree_visible_character_count,
        )
        for item in plan.index
    ) == (
        (
            "lesson-root",
            None,
            0,
            PlanningEligibility.CONTEXT_ONLY,
            PlanningPriority.NONE,
            11,
            5_211,
        ),
        (
            "framework",
            "lesson-root",
            1,
            PlanningEligibility.ELIGIBLE,
            PlanningPriority.SUPPORTING,
            5_000,
            5_000,
        ),
        (
            "details",
            "lesson-root",
            2,
            PlanningEligibility.ELIGIBLE,
            PlanningPriority.SUPPORTING,
            100,
            200,
        ),
        (
            "fragile-fact",
            "details",
            3,
            PlanningEligibility.ELIGIBLE,
            PlanningPriority.SUPPORTING,
            100,
            100,
        ),
    )
    assert tuple(
        (
            bundle.kind,
            bundle.active_topic_keys,
            tuple(key for slot in bundle.slots for key in slot.paragraph_keys),
            bundle.visible_character_count,
        )
        for bundle in plan.bundles
    ) == (
        (
            PlannedBundleKind.TOPIC_GROUP,
            ("framework",),
            ("paragraph-0", "paragraph-1"),
            5_000,
        ),
        (
            PlannedBundleKind.TOPIC_GROUP,
            ("details", "fragile-fact"),
            ("paragraph-2", "paragraph-3"),
            200,
        ),
    )


def test_exact_character_and_slot_limits_pass_and_adjacent_topics_combine() -> None:
    exact_characters = plan_flashcard_lesson(_unit_for_sizes((2_500, 2_500)))
    exact_slots = plan_flashcard_lesson(_unit_for_sizes((1,) * MAX_PLANNED_EVIDENCE_SLOTS))

    assert len(exact_characters.bundles) == 1
    assert exact_characters.bundles[0].visible_character_count == 5_000
    assert exact_characters.bundles[0].soft_limit_exceeded is False
    assert len(exact_slots.bundles) == 1
    assert len(exact_slots.bundles[0].slots) == 24
    assert max(len(item.slots) for item in exact_slots.bundles) <= 24

    over_slots = plan_flashcard_lesson(_unit_for_sizes((1,) * (MAX_PLANNED_EVIDENCE_SLOTS + 1)))
    assert tuple(len(bundle.slots) for bundle in over_slots.bundles) == (24, 1)


def test_bundler_keeps_a_complete_eligible_subtree_together_when_it_fits() -> None:
    paragraphs = (
        _paragraph(0, "a", 4_500, start=0),
        _paragraph(1, "b", 400, start=4_500),
        _paragraph(2, "b-child", 400, start=4_900),
    )
    unit = LessonGenerationUnit(
        "subtree-packing",
        "Subtree packing",
        (
            _topic("a", 0, paragraph_keys=("paragraph-0",), start=0, end=4_500),
            _topic("b", 1, paragraph_keys=("paragraph-1",), start=4_500, end=5_300),
            _topic(
                "b-child",
                2,
                level=2,
                parent="b",
                paragraph_keys=("paragraph-2",),
                start=4_900,
                end=5_300,
            ),
        ),
        paragraphs,
    )

    plan = plan_flashcard_lesson(unit)

    assert tuple(bundle.active_topic_keys for bundle in plan.bundles) == (
        ("a",),
        ("b", "b-child"),
    )
    assert tuple(bundle.visible_character_count for bundle in plan.bundles) == (4_500, 800)


def test_eligible_descendant_across_context_node_is_emitted_once_with_root_subtree() -> None:
    paragraphs = (
        _paragraph(0, "root", 400, start=0),
        _paragraph(1, "grandchild", 400, start=400),
    )
    unit = LessonGenerationUnit(
        "context-gap",
        "Context gap",
        (
            _topic("root", 0, paragraph_keys=("paragraph-0",), start=0, end=800),
            _topic("context", 1, level=2, parent="root", start=400, end=800),
            _topic(
                "grandchild",
                2,
                level=3,
                parent="context",
                paragraph_keys=("paragraph-1",),
                start=400,
                end=800,
            ),
        ),
        paragraphs,
    )

    plan = plan_flashcard_lesson(unit)

    assert len(plan.bundles) == 1
    assert plan.bundles[0].active_topic_keys == ("root", "grandchild")
    assert tuple(
        key for slot in plan.bundles[0].slots for key in slot.paragraph_keys
    ) == ("paragraph-0", "paragraph-1")


def test_oversized_topic_splits_only_between_whole_paragraphs() -> None:
    plan = plan_flashcard_lesson(_unit_for_sizes((3_000, 2_000, 1)))

    assert tuple(
        tuple(key for slot in bundle.slots for key in slot.paragraph_keys)
        for bundle in plan.bundles
    ) == (("paragraph-0", "paragraph-1"), ("paragraph-2",))
    assert tuple(bundle.kind for bundle in plan.bundles) == (
        PlannedBundleKind.PARAGRAPH_SPLIT,
        PlannedBundleKind.PARAGRAPH_SPLIT,
    )


def test_one_oversized_paragraph_is_retained_whole_and_marked_truthfully() -> None:
    plan = plan_flashcard_lesson(_unit_for_sizes((DEFAULT_BUNDLE_VISIBLE_CHARACTER_TARGET + 1,)))

    assert len(plan.bundles) == 1
    assert plan.bundles[0].kind is PlannedBundleKind.OVERSIZED_PARAGRAPH
    assert plan.bundles[0].slots[0].paragraph_keys == ("paragraph-0",)
    assert plan.bundles[0].soft_limit_exceeded is True


def test_index_bounds_are_closed_and_never_truncated() -> None:
    one = LessonGenerationUnit("one", "One", (_topic("topic-0", 0),), ())
    maximum = LessonGenerationUnit(
        "maximum",
        "Maximum",
        tuple(_topic(f"topic-{position}", position) for position in range(256)),
        (),
    )

    assert len(plan_flashcard_lesson(one).index) == 1
    assert len(plan_flashcard_lesson(maximum).index) == MAX_LESSON_INDEX_ENTRIES
    with pytest.raises(ValueError, match="lesson_index_limit_exceeded"):
        LessonGenerationUnit(
            "too-large",
            "Too large",
            tuple(_topic(f"topic-{position}", position) for position in range(257)),
            (),
        )


def test_empty_context_only_explicit_skip_and_one_topic_plans_are_valid() -> None:
    empty = LessonGenerationUnit("empty", "Empty", (), ())
    context_only = LessonGenerationUnit("context", "Context", (_topic("container", 0),), ())
    skipped_paragraph = _paragraph(0, "skip", 100, start=0)
    skipped = LessonGenerationUnit(
        "skipped",
        "Skipped",
        (
            _topic(
                "skip",
                0,
                paragraph_keys=(skipped_paragraph.paragraph_key,),
                end=100,
                eligibility=PlanningEligibility.EXCLUDED,
                priority=PlanningPriority.NONE,
            ),
        ),
        (skipped_paragraph,),
    )
    one_topic = _unit_for_sizes((100,))

    assert plan_flashcard_lesson(empty).bundles == ()
    assert plan_flashcard_lesson(context_only).bundles == ()
    assert plan_flashcard_lesson(skipped).bundles == ()
    assert len(plan_flashcard_lesson(one_topic).bundles) == 1


def test_multiple_sources_remain_distinct_and_ordered() -> None:
    paragraphs = (
        _paragraph(0, "primary", 50, start=0, source="primary", revision="primary-r1"),
        _paragraph(1, "supplement", 60, start=0, source="supplement", revision="supplement-r1"),
    )
    unit = LessonGenerationUnit(
        "multi-source",
        "Multi source",
        (
            _topic(
                "primary",
                0,
                paragraph_keys=("paragraph-0",),
                end=50,
                source="primary",
                revision="primary-r1",
            ),
            _topic(
                "supplement",
                1,
                paragraph_keys=("paragraph-1",),
                end=60,
                source="supplement",
                revision="supplement-r1",
            ),
        ),
        paragraphs,
    )

    plan = plan_flashcard_lesson(unit)
    assert tuple(slot.span.source_id for slot in plan.bundles[0].slots) == (
        SourceId("primary"),
        SourceId("supplement"),
    )


class _CorePolicy:
    policy_id = "fixture.core"
    policy_version = "1"
    policy_fingerprint = "c" * 64

    def classify(self, unit: LessonGenerationUnit) -> FlashcardPlanningPolicyReceipt:
        return FlashcardPlanningPolicyReceipt.issue(
            self.policy_id,
            self.policy_version,
            self.policy_fingerprint,
            tuple(
                TopicPlanningClassification(
                    topic.topic_key,
                    PlanningEligibility.ELIGIBLE,
                    PlanningPriority.CORE,
                )
                for topic in unit.topics
            ),
        )


def test_trusted_policy_may_classify_unset_topics_but_not_override_declarations() -> None:
    unit = _unit_for_sizes((100,))
    assert plan_flashcard_lesson(unit, _CorePolicy()).index[0].priority is PlanningPriority.CORE

    declared = replace(
        unit,
        topics=(
            replace(
                unit.topics[0],
                declared_eligibility=PlanningEligibility.EXCLUDED,
                declared_priority=PlanningPriority.NONE,
            ),
        ),
    )
    with pytest.raises(ValueError, match="cannot override trusted declared"):
        plan_flashcard_lesson(declared, _CorePolicy())


@pytest.mark.parametrize(
    ("topics", "paragraphs", "error"),
    (
        (
            (_topic("child", 0, level=2, parent="parent"), _topic("parent", 1)),
            (),
            "parents must exist before",
        ),
        (
            (_topic("root", 0), _topic("child", 1, level=3, parent="root")),
            (),
            "exactly one deeper",
        ),
        (
            (_topic("topic", 0, paragraph_keys=("paragraph-0",), end=100),),
            (
                _paragraph(0, "topic", 70, start=0),
                LessonParagraph("paragraph-1", "topic", 1, _span(50, 90), 40),
            ),
            "non-overlapping",
        ),
        (
            (_topic("topic", 0, paragraph_keys=("paragraph-0",), end=100),),
            (_paragraph(0, "unknown", 100, start=0),),
            "owner topic is unknown",
        ),
        (
            (_topic("topic", 0, paragraph_keys=("paragraph-0",), end=50),),
            (_paragraph(0, "topic", 100, start=0),),
            "must contain",
        ),
    ),
)
def test_host_structure_rejects_missing_parents_nesting_overlap_and_unknown_ownership(
    topics: tuple[LessonTopic, ...],
    paragraphs: tuple[LessonParagraph, ...],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        LessonGenerationUnit("invalid", "Invalid", topics, paragraphs)


def test_host_structure_rejects_preorder_reentry_into_a_closed_subtree() -> None:
    topics = (
        _topic("a", 0),
        _topic("a-child", 1, level=2, parent="a"),
        _topic("b", 2),
        _topic("a-reentry", 3, level=2, parent="a"),
    )

    with pytest.raises(ValueError, match="cannot re-enter a closed subtree"):
        LessonGenerationUnit("reentry", "Reentry", topics, ())


@pytest.mark.parametrize(
    "mutate",
    (
        lambda value: value.update(provider="forbidden"),
        lambda value: value.pop("plan_fingerprint"),
        lambda value: value.update(plan_fingerprint="f" * 64),
        lambda value: value.update(index={"not": "an array"}),
        lambda value: value["index"][1].update(priority="core"),
        lambda value: value["bundles"].reverse(),
        lambda value: value["bundles"][0]["slots"].pop(),
        lambda value: value["policy_receipt"].update(receipt_fingerprint="e" * 64),
        lambda value: value["unit"]["topics"][0].update(extra="forbidden"),
    ),
)
def test_plan_codec_fails_closed_on_malformed_reordered_gapped_and_forged_values(
    mutate: Any,
) -> None:
    payload = cast(dict[str, Any], _plain(plan_flashcard_lesson(_nested_unit()).to_json()))
    mutate(payload)

    with pytest.raises((TypeError, ValueError)):
        FlashcardLessonPlan.from_json(cast(Mapping[str, JsonValue], freeze_json(payload)))


def test_plan_and_policy_codecs_reject_noncanonical_bytes() -> None:
    plan = plan_flashcard_lesson(_nested_unit())
    noncanonical_plan = json.dumps(_plain(plan.to_json()), indent=2).encode()
    noncanonical_receipt = json.dumps(_plain(plan.policy_receipt.to_json()), indent=2).encode()

    with pytest.raises(ValueError, match="canonical"):
        FlashcardLessonPlan.from_bytes(noncanonical_plan)
    with pytest.raises(ValueError, match="canonical"):
        FlashcardPlanningPolicyReceipt.from_bytes(noncanonical_receipt)


def test_plan_fingerprint_commits_exact_domain_separated_plan_payload() -> None:
    plan = plan_flashcard_lesson(_nested_unit())
    payload = {
        "unit": _plain(plan.unit.to_json()),
        "index": _plain(tuple(item.to_json() for item in plan.index)),
        "bundles": _plain(tuple(item.to_json() for item in plan.bundles)),
        "configuration": {
            "bundle_visible_character_target": plan.bundle_visible_character_target,
            "max_evidence_slots_per_bundle": plan.max_evidence_slots_per_bundle,
        },
        "policy_receipt": _plain(plan.policy_receipt.to_json()),
    }
    canonical = json.dumps(
        payload,
        allow_nan=False,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()

    assert plan.plan_fingerprint == sha256(b"flashcard-lesson-plan@1\0" + canonical).hexdigest()
    assert b"plan_fingerprint" not in canonical


def test_prepared_scope_bytes_and_private_tool_contract_remain_byte_compatible() -> None:
    prepared = _prepared_scope(("framework", "details", "fragile-fact"))
    original_bytes = prepared.to_bytes()
    decoded_json = json.loads(original_bytes)

    assert isinstance(decoded_json["index"], list)
    assert isinstance(decoded_json["evidence"]["items"], list)
    assert PreparedFlashcardScope.from_bytes(original_bytes).to_bytes() == original_bytes
    assert set(prepared.to_json()) == {"index", "evidence", "scope_fingerprint"}


def test_prepared_planned_wrapper_binds_scope_plan_bundle_topics_and_classifications() -> None:
    plan = plan_flashcard_lesson(_nested_unit())
    bundle = plan.bundles[1]
    prepared = _prepared_scope(bundle.active_topic_keys)
    classifications = tuple(
        TopicPlanningClassification(key, PlanningEligibility.ELIGIBLE, PlanningPriority.SUPPORTING)
        for key in bundle.active_topic_keys
    )
    wrapper = PreparedPlannedFlashcardScope.prepare(
        prepared,
        plan,
        bundle.bundle_id,
    )

    decoded = PreparedPlannedFlashcardScope.from_bytes(wrapper.to_bytes())
    decoded.validate_against_plan(plan)
    assert decoded.to_bytes() == wrapper.to_bytes()
    assert decoded.wrapper_fingerprint == wrapper.wrapper_fingerprint
    assert decoded.classifications == classifications
    with pytest.raises(ValueError, match="not present"):
        PreparedPlannedFlashcardScope.prepare(prepared, plan, "another-bundle")
    for field, forged in (
        ("plan_fingerprint", "f" * 64),
        ("bundle_id", "another-bundle"),
        ("active_topic_keys", [bundle.active_topic_keys[-1]]),
    ):
        payload = cast(dict[str, Any], _plain(wrapper.to_json()))
        payload[field] = forged
        with pytest.raises((TypeError, ValueError)):
            PreparedPlannedFlashcardScope.from_json(
                cast(Mapping[str, JsonValue], freeze_json(payload))
            )


def test_navigation_index_is_not_an_evidence_allowlist_or_card_quota() -> None:
    plan = plan_flashcard_lesson(_nested_unit())
    index_fields = set(plan.index[0].to_json())
    slot_fields = set(plan.bundles[0].slots[0].to_json())

    assert index_fields.isdisjoint(
        {"evidence_handle", "evidence_handles", "citation", "card_count", "quota"}
    )
    assert {"paragraph_keys", "span"}.issubset(slot_fields)
    assert all(len(bundle.slots) <= MAX_PLANNED_EVIDENCE_SLOTS for bundle in plan.bundles)
    assert all(
        field not in plan.to_json()
        for field in ("card_count", "minimum_cards", "target_cards", "anki", "model")
    )
