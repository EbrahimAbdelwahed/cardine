from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from types import MappingProxyType
from typing import cast

import pytest

from study_agent.domain import (
    Actor,
    AnswerProvenance,
    AnswerSegment,
    AnswerStatus,
    BlobId,
    BlobRef,
    ChunkId,
    Citation,
    CorrelationId,
    CourseId,
    CourseProfile,
    DomainEvent,
    EventId,
    GroundedAnswer,
    InteractionId,
    InteractionKind,
    InteractionRecord,
    ModelProvenance,
    PrincipalKind,
    PromptProvenance,
    RetrievalProvenance,
    RevisionId,
    RunId,
    SegmentKind,
    SessionId,
    SessionStatus,
    SourceChunk,
    SourceCommitment,
    SourceId,
    SourcePolicy,
    StudySession,
    TerminologyEntry,
    TerminologyPolicy,
    ValidatorProvenance,
    VersionPins,
)
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object


def test_frozen_json_owns_untrusted_proxies_and_keeps_mapping_equality() -> None:
    nested: dict[str, JsonValue] = {"values": cast(JsonValue, [1, 2])}
    caller: dict[str, JsonValue] = {"nested": MappingProxyType(nested)}
    frozen = freeze_object(MappingProxyType(caller))
    nested["values"] = (9,)
    caller.clear()
    expected = {"nested": {"values": (1, 2)}}
    assert frozen == expected
    assert expected == frozen
    with pytest.raises(TypeError):
        frozen["new"] = True  # type: ignore[index]
    assert freeze_object(frozen) == frozen


def test_event_envelope_is_versioned_sequenced_and_deeply_immutable() -> None:
    event = DomainEvent(
        event_id=EventId("evt-1"),
        course_id=CourseId("course-1"),
        course_sequence=1,
        event_type="course.created",
        schema_version=1,
        actor=Actor(PrincipalKind.HUMAN, "local-user"),
        occurred_at=datetime.now(UTC),
        correlation_id=CorrelationId("corr-1"),
        payload=cast(JsonObject, {"nested": {"value": [1, 2]}}),
    )

    with pytest.raises(FrozenInstanceError):
        event.course_sequence = 2  # type: ignore[misc]
    with pytest.raises(TypeError):
        event.payload["new"] = True  # type: ignore[index]
    assert event.payload["nested"] == {"value": (1, 2)}


@pytest.mark.parametrize("sequence,version", [(0, 1), (1, 0)])
def test_event_envelope_rejects_invalid_sequence_or_version(sequence: int, version: int) -> None:
    with pytest.raises(ValueError):
        DomainEvent(
            EventId("evt"),
            CourseId("course"),
            sequence,
            "course.created",
            version,
            Actor(PrincipalKind.HUMAN, "user"),
            datetime.now(UTC),
            CorrelationId("corr"),
        )


def test_source_chunk_requires_a_stable_non_empty_span_and_checksum() -> None:
    with pytest.raises(ValueError, match="non-empty forward span"):
        SourceChunk(
            ChunkId("chunk"),
            SourceId("source"),
            RevisionId("revision"),
            5,
            5,
            (),
            0,
            "a" * 64,
            "chunker-v1",
        )


def test_blob_reference_requires_content_address_metadata() -> None:
    ref = BlobRef(BlobId("sha256:abc"), "a" * 64, 12)
    assert ref.byte_length == 12
    with pytest.raises(ValueError, match="SHA-256"):
        BlobRef(BlobId("bad"), "ABC", 12)


def test_domain_contracts_take_ownership_of_caller_collections() -> None:
    roles = ["primary"]
    terms = [TerminologyEntry("heart", "cuore")]
    styles = ["oral"]
    goals = ["Explain cardiac anatomy"]
    policy = SourcePolicy(roles)  # type: ignore[arg-type]
    terminology = TerminologyPolicy(terms)  # type: ignore[arg-type]
    course = CourseProfile(
        CourseId("course"),
        "Anatomy",
        "it",
        assessment_styles=styles,  # type: ignore[arg-type]
        learning_goals=goals,  # type: ignore[arg-type]
        source_policy=policy,
        terminology_policy=terminology,
    )
    citation = Citation(
        SourceId("source"), RevisionId("revision"), ChunkId("chunk"), 0, 4, "p. 1"
    )
    citations = [citation]
    segment = AnswerSegment(
        SegmentKind.SUPPORTED_CLAIM,
        "The claim.",
        citations,  # type: ignore[arg-type]
    )
    validators = [
        ValidatorProvenance(
            "grounded_answer_integrity", "1", True, "continue", "b" * 64
        )
    ]
    provenance = AnswerProvenance(
        (SourceCommitment(SourceId("source"), RevisionId("revision"), ChunkId("chunk"), 0, 4),),
        PromptProvenance("grounded-answer", "1", "c" * 64, ("d" * 64,)),
        ModelProvenance("fake", "1", "scripted", "response-1", RunId("run-1")),
        RetrievalProvenance("lexical", "1", "e" * 64, "index-1", "f" * 64),
        validators,  # type: ignore[arg-type]
        VersionPins(
            "grounded_answer@1",
            "grounded_answer_flow@1",
            "grounded_answer@1",
            "fake@1",
            "session@1",
            "tools@1",
        ),
        RunId("run-1"),
    )
    segments = [segment]
    answer = GroundedAnswer(
        AnswerStatus.ANSWERED,
        segments,  # type: ignore[arg-type]
        None,
        provenance,
    )
    interactions = [
        InteractionRecord(
            InteractionId("interaction"),
            InteractionKind.HUMAN,
            datetime.now(UTC),
            "Question",
        )
    ]
    session = StudySession(
        SessionId("session"),
        CourseId("course"),
        SessionStatus.ACTIVE,
        interactions[0].occurred_at,
        interaction_ids=(interactions[0].id,),
    )

    roles.append("secondary")
    terms.clear()
    styles.clear()
    goals.clear()
    citations.clear()
    validators.clear()
    segments.clear()
    interactions.clear()

    assert policy.allowed_roles == ("primary",)
    assert terminology.entries[0].preferred_term == "cuore"
    assert course.assessment_styles == ("oral",)
    assert course.learning_goals == ("Explain cardiac anatomy",)
    assert segment.citations == (citation,)
    assert provenance.validators
    assert answer.segments == (segment,)
    assert len(session.interaction_ids) == 1


@pytest.mark.parametrize(
    "factory",
    [
        lambda: SourcePolicy("primary"),  # type: ignore[arg-type]
        lambda: SourcePolicy((1,)),  # type: ignore[arg-type]
        lambda: SourcePolicy((), True),
        lambda: TerminologyPolicy("heart"),  # type: ignore[arg-type]
        lambda: TerminologyPolicy(("heart",)),  # type: ignore[arg-type]
        lambda: CourseProfile(
            CourseId("course"),
            "Course",
            "en",
            assessment_styles="oral",  # type: ignore[arg-type]
            learning_goals=("Learn",),
        ),
        lambda: CourseProfile(
            CourseId("course"),
            "Course",
            "en",
            assessment_styles=(1,),  # type: ignore[arg-type]
            learning_goals=("Learn",),
        ),
        lambda: CourseProfile(
            CourseId("course"),
            "Course",
            "en",
            learning_goals=(1,),  # type: ignore[arg-type]
        ),
    ],
)
def test_course_contracts_reject_strings_as_collections_and_wrong_item_types(
    factory: object,
) -> None:
    with pytest.raises(TypeError):
        factory()  # type: ignore[operator]
