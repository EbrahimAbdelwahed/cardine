from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import cast

from ._validation import JsonObject, require_text


@dataclass(frozen=True, slots=True)
class Identifier:
    """A validated, immutable identifier with type-sensitive equality."""

    value: str

    def __post_init__(self) -> None:
        require_text(self.value, type(self).__name__)

    def __str__(self) -> str:
        return self.value


class CourseId(Identifier):
    pass


class SourceId(Identifier):
    pass


class RevisionId(Identifier):
    pass


class ChunkId(Identifier):
    pass


class SessionId(Identifier):
    pass


class InteractionId(Identifier):
    pass


class TutorPresentationId(Identifier):
    """Stable identity of one validated host presentation."""

    pass


class StatementId(Identifier):
    pass


class AnswerId(Identifier):
    pass


class EventId(Identifier):
    pass


class RunId(Identifier):
    pass


class ModelRunId(Identifier):
    pass


class CorrelationId(Identifier):
    pass


class BlobId(Identifier):
    pass


class SubstrateId(Identifier):
    """Content identity of one frozen normalized-text byte sequence."""

    def __post_init__(self) -> None:
        super().__post_init__()
        prefix = "substrate:sha256:"
        digest = self.value.removeprefix(prefix)
        if not self.value.startswith(prefix) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError("substrate id must be substrate:sha256:<lowercase sha256>")


class SubstrateProductionId(Identifier):
    """Identity of one immutable substrate conversion/admission receipt."""

    def __post_init__(self) -> None:
        super().__post_init__()
        prefix = "substrate-production:sha256:"
        digest = self.value.removeprefix(prefix)
        if not self.value.startswith(prefix) or len(digest) != 64 or any(
            character not in "0123456789abcdef" for character in digest
        ):
            raise ValueError(
                "substrate production id must be "
                "substrate-production:sha256:<lowercase sha256>"
            )


class ArtifactId(Identifier):
    pass


class ArtifactRevisionId(Identifier):
    pass


class ArtifactBatchId(Identifier):
    pass


class PresentationId(Identifier):
    pass


class AttemptId(Identifier):
    pass


class GradeId(Identifier):
    pass


class ReviewId(Identifier):
    """Stable identity of one learner review of one artifact revision."""

    pass


class ScheduleDecisionId(Identifier):
    """Stable identity of one applied scheduling decision."""

    pass


def artifact_event_id_for(
    course_id: CourseId,
    session_id: SessionId,
    retry_identity: str,
    command_kind: str,
) -> EventId:
    require_text(retry_identity, "retry_identity")
    require_text(command_kind, "command_kind")
    payload = (
        f"artifact-event@1\0{course_id}\0{session_id}\0{retry_identity}\0{command_kind}"
    ).encode()
    return EventId(f"event-sha256:{sha256(payload).hexdigest()}")


def substrate_id_for(content: bytes) -> SubstrateId:
    """Derive the exact content identity for frozen normalized UTF-8 bytes."""
    if not isinstance(content, bytes):
        raise TypeError("substrate content must be bytes")
    if not content:
        raise ValueError("substrate content must be non-empty")
    try:
        if not content.decode("utf-8", errors="strict"):
            raise ValueError("substrate content must be non-empty UTF-8 text")
    except UnicodeDecodeError as error:
        raise ValueError("substrate content must be valid UTF-8") from error
    return SubstrateId(f"substrate:sha256:{sha256(content).hexdigest()}")


def substrate_production_id_for(
    *,
    source_id: SourceId,
    original_blob_id: str,
    original_blob_sha256: str,
    original_blob_byte_length: int,
    substrate_id: SubstrateId,
    converter_name: str,
    converter_version: str,
    normalization_version: str,
    page_map_policy_version: str,
    page_count: int | None,
    page_map: Sequence[Mapping[str, object]],
    admission_policy_version: str,
    character_length: int | None = None,
) -> SubstrateProductionId:
    """Derive the domain-separated production identity.

    ``produced_at`` intentionally does not appear: an exact retry must return
    the first committed receipt rather than create a second production.
    """
    if not isinstance(source_id, SourceId):
        raise TypeError("substrate production requires SourceId")
    for value, field_name in (
        (original_blob_id, "original_blob_id"),
        (original_blob_sha256, "original_blob_sha256"),
        (converter_name, "converter_name"),
        (converter_version, "converter_version"),
        (normalization_version, "normalization_version"),
        (page_map_policy_version, "page_map_policy_version"),
        (admission_policy_version, "admission_policy_version"),
    ):
        require_text(value, field_name)
    if (
        len(original_blob_sha256) != 64
        or any(character not in "0123456789abcdef" for character in original_blob_sha256)
        or original_blob_id != f"sha256:{original_blob_sha256}"
    ):
        raise ValueError("original blob identity is not a canonical SHA-256 binding")
    if type(original_blob_byte_length) is not int or original_blob_byte_length < 0:
        raise ValueError("original blob byte length must be a non-negative integer")
    if not isinstance(substrate_id, SubstrateId):
        raise TypeError("substrate production requires SubstrateId")
    if page_count is not None and (type(page_count) is not int or page_count < 1):
        raise ValueError("page_count must be positive when present")
    if character_length is not None and (
        type(character_length) is not int or character_length < 1
    ):
        raise ValueError("character_length must be positive when present")
    if page_count is not None and character_length is None:
        raise ValueError("character_length is required when pagination is present")
    if not isinstance(page_map, Sequence) or isinstance(page_map, (str, bytes, bytearray)):
        raise ValueError("page_map must be a sequence of {offset,page} objects")
    entries = tuple(page_map)
    if page_count is None and entries:
        raise ValueError("page_map must be empty when page_count is absent")
    if page_count is not None and not entries:
        raise ValueError("page_map cannot be empty when page_count is present")
    previous_offset = -1
    previous_page = 0
    canonical_page_map: list[Mapping[str, object]] = []
    for index, entry in enumerate(entries):
        if not isinstance(entry, Mapping) or set(entry) != {"offset", "page"}:
            raise ValueError(f"page_map[{index}] must contain exactly offset and page")
        offset = entry["offset"]
        page = entry["page"]
        if type(offset) is not int or type(page) is not int:
            raise ValueError(f"page_map[{index}] offset and page must be integers")
        if offset < 0 or page < 1:
            raise ValueError(f"page_map[{index}] offset/page bounds are invalid")
        if index == 0 and offset != 0:
            raise ValueError("page_map must begin at offset zero")
        if offset <= previous_offset or page <= previous_page:
            raise ValueError("page_map offsets/pages must be strictly increasing")
        if character_length is not None and offset >= character_length:
            raise ValueError("page_map offset exceeds character_length")
        if page_count is not None and page > page_count:
            raise ValueError("page_map page exceeds page_count")
        previous_offset = offset
        previous_page = page
        canonical_page_map.append({"offset": offset, "page": page})
    from study_agent.state.serialization import canonical_json_bytes

    identity = {
        "admission_policy_version": admission_policy_version,
        "converter_name": converter_name,
        "converter_version": converter_version,
        "normalization_version": normalization_version,
        "original_blob": {
            "byte_length": original_blob_byte_length,
            "checksum_sha256": original_blob_sha256,
            "id": original_blob_id,
        },
        "page_count": page_count,
        "page_map_policy_version": page_map_policy_version,
        "page_map": tuple(canonical_page_map),
        "source_id": str(source_id),
        "substrate_id": str(substrate_id),
    }
    digest = sha256(
        b"study-agent/substrate-production/v1\0"
        + canonical_json_bytes(cast(JsonObject, identity))
    ).hexdigest()
    return SubstrateProductionId(f"substrate-production:sha256:{digest}")


def substrate_production_event_id_for(
    course_id: CourseId,
    production_id: SubstrateProductionId,
    course_sequence: int,
) -> EventId:
    if not isinstance(course_id, CourseId) or not isinstance(
        production_id, SubstrateProductionId
    ):
        raise TypeError("substrate production event requires typed ids")
    if type(course_sequence) is not int or course_sequence < 1:
        raise ValueError("course_sequence must be positive")
    from study_agent.state.serialization import canonical_json_bytes

    payload = canonical_json_bytes(
        {
            "course_id": str(course_id),
            "course_sequence": course_sequence,
            "substrate_production_id": str(production_id),
        }
    )
    digest = sha256(b"study-agent/source-substrate-produced/v1\0" + payload).hexdigest()
    return EventId(f"event-sha256:{digest}")


def presentation_id_for(
    course_id: CourseId,
    session_id: SessionId,
    revision_id: ArtifactRevisionId,
    retry_identity: str,
) -> PresentationId:
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("presentation identity requires typed course and session ids")
    if not isinstance(revision_id, ArtifactRevisionId):
        raise TypeError("presentation identity requires ArtifactRevisionId")
    return PresentationId(
        "presentation-sha256:"
        f"{_assessment_digest(course_id, session_id, revision_id, retry_identity, 'presentation')}"
    )


def attempt_id_for(
    course_id: CourseId,
    session_id: SessionId,
    presentation_id: PresentationId,
    retry_identity: str,
) -> AttemptId:
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("attempt identity requires typed course and session ids")
    if not isinstance(presentation_id, PresentationId):
        raise TypeError("attempt identity requires PresentationId")
    return AttemptId(
        "attempt-sha256:"
        f"{_assessment_digest(course_id, session_id, presentation_id, retry_identity, 'attempt')}"
    )


def grade_id_for(
    course_id: CourseId,
    session_id: SessionId,
    attempt_id: AttemptId,
    retry_identity: str,
) -> GradeId:
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("grade identity requires typed course and session ids")
    if not isinstance(attempt_id, AttemptId):
        raise TypeError("grade identity requires AttemptId")
    return GradeId(
        "grade-sha256:"
        f"{_assessment_digest(course_id, session_id, attempt_id, retry_identity, 'grade')}"
    )


def review_id_for(
    course_id: CourseId,
    session_id: SessionId,
    revision_id: ArtifactRevisionId,
    retry_identity: str,
) -> ReviewId:
    """Derive review identity from trusted scope and retry inputs only."""
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("review identity requires typed course and session ids")
    if not isinstance(revision_id, ArtifactRevisionId):
        raise TypeError("review identity requires ArtifactRevisionId")
    require_text(retry_identity, "retry_identity")
    raw = f"recall-review@1\0{course_id}\0{session_id}\0{revision_id}\0{retry_identity}".encode()
    return ReviewId(f"review-sha256:{sha256(raw).hexdigest()}")


def schedule_decision_id_for(
    course_id: CourseId,
    session_id: SessionId,
    revision_id: ArtifactRevisionId,
    trigger: str,
    identity: str,
) -> ScheduleDecisionId:
    """Derive an enrollment/review decision identity without scheduler output."""
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("schedule identity requires typed course and session ids")
    if not isinstance(revision_id, ArtifactRevisionId):
        raise TypeError("schedule identity requires ArtifactRevisionId")
    require_text(trigger, "trigger")
    require_text(identity, "identity")
    raw = (
        f"recall-schedule@1\0{course_id}\0{session_id}\0"
        f"{revision_id}\0{trigger}\0{identity}"
    ).encode()
    return ScheduleDecisionId(f"schedule-sha256:{sha256(raw).hexdigest()}")


def enrollment_decision_id_for(
    course_id: CourseId,
    session_id: SessionId,
    revision_id: ArtifactRevisionId,
    retry_identity: str,
) -> ScheduleDecisionId:
    return schedule_decision_id_for(
        course_id, session_id, revision_id, "enrollment", retry_identity
    )


def review_decision_id_for(
    course_id: CourseId,
    session_id: SessionId,
    revision_id: ArtifactRevisionId,
    review_id: ReviewId,
) -> ScheduleDecisionId:
    if not isinstance(review_id, ReviewId):
        raise TypeError("review decision identity requires ReviewId")
    return schedule_decision_id_for(
        course_id, session_id, revision_id, "review", str(review_id)
    )


def recall_event_id_for(
    course_id: CourseId,
    session_id: SessionId,
    retry_identity: str,
    event_type: str,
) -> EventId:
    require_text(retry_identity, "retry_identity")
    require_text(event_type, "event_type")
    raw = f"recall-event@1\0{course_id}\0{session_id}\0{retry_identity}\0{event_type}".encode()
    return EventId(f"event-sha256:{sha256(raw).hexdigest()}")


def assessment_event_id_for(
    course_id: CourseId,
    session_id: SessionId,
    retry_identity: str,
    event_type: str,
) -> EventId:
    require_text(retry_identity, "retry_identity")
    require_text(event_type, "event_type")
    payload = (
        f"assessment-event@1\0{course_id}\0{session_id}\0{retry_identity}\0{event_type}"
    ).encode()
    return EventId(f"event-sha256:{sha256(payload).hexdigest()}")


def _assessment_digest(
    course_id: CourseId,
    session_id: SessionId,
    target_id: Identifier,
    retry_identity: str,
    purpose: str,
) -> str:
    require_text(retry_identity, "retry_identity")
    payload = (
        f"assessment-identity@1\0{course_id}\0{session_id}\0{target_id}\0"
        f"{retry_identity}\0{purpose}"
    ).encode()
    return sha256(payload).hexdigest()


def answer_id_for(
    course_id: CourseId,
    session_id: SessionId,
    run_id: RunId,
    idempotency_key: str,
) -> AnswerId:
    return AnswerId(
        f"answer-sha256:{_retry_digest(course_id, session_id, run_id, idempotency_key, 'answer')}"
    )


def question_interaction_id_for(
    course_id: CourseId,
    session_id: SessionId,
    run_id: RunId,
    idempotency_key: str,
) -> InteractionId:
    return InteractionId(
        "interaction-sha256:"
        f"{_retry_digest(course_id, session_id, run_id, idempotency_key, 'question')}"
    )


def assistant_interaction_id_for(
    course_id: CourseId,
    session_id: SessionId,
    run_id: RunId,
    idempotency_key: str,
) -> InteractionId:
    return InteractionId(
        "interaction-sha256:"
        f"{_retry_digest(course_id, session_id, run_id, idempotency_key, 'assistant')}"
    )


def learner_interaction_id_for(
    course_id: CourseId,
    session_id: SessionId,
    idempotency_key: str,
) -> InteractionId:
    require_text(idempotency_key, "idempotency_key")
    identity = f"session-learner-turn@1\0{course_id}\0{session_id}\0{idempotency_key}".encode()
    return InteractionId(f"interaction-sha256:{sha256(identity).hexdigest()}")


def session_turn_event_id_for(
    course_id: CourseId,
    session_id: SessionId,
    idempotency_key: str,
    event_type: str,
) -> EventId:
    require_text(idempotency_key, "idempotency_key")
    require_text(event_type, "event_type")
    identity = (
        f"session-turn-event@1\0{course_id}\0{session_id}\0"
        f"{idempotency_key}\0{event_type}"
    ).encode()
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


def tutor_presentation_id_for(
    course_id: CourseId,
    session_id: SessionId,
    host_turn_id: str,
    kind: str,
) -> TutorPresentationId:
    """Derive a presentation identity from the trusted host receipt identity."""
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("tutor presentation identity requires typed course and session ids")
    require_text(host_turn_id, "host_turn_id")
    require_text(kind, "kind")
    payload = (
        f"tutor-presentation@1\0{course_id}\0{session_id}\0"
        f"{host_turn_id}\0{kind}"
    ).encode()
    return TutorPresentationId(
        f"tutor-presentation-sha256:{sha256(payload).hexdigest()}"
    )


def session_event_id_for(
    course_id: CourseId,
    session_id: SessionId,
    run_id: RunId,
    idempotency_key: str,
    event_type: str,
) -> EventId:
    require_text(event_type, "event_type")
    return EventId(
        "event-sha256:"
        f"{_retry_digest(course_id, session_id, run_id, idempotency_key, event_type)}"
    )


def study_context_event_id_for(
    course_id: CourseId,
    session_id: SessionId,
    idempotency_key: str,
    command_kind: str,
) -> EventId:
    """Return the stable identity of one study-context command."""
    require_text(idempotency_key, "idempotency_key")
    require_text(command_kind, "command_kind")
    identity = (
        f"study-context@1\0{course_id}\0{session_id}\0{idempotency_key}\0{command_kind}"
    ).encode()
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


def statement_id_for(command_event_id: EventId) -> StatementId:
    """Derive a statement identity from its canonical record command."""
    identity = f"study-context-statement@1\0{command_event_id}".encode()
    return StatementId(f"statement-sha256:{sha256(identity).hexdigest()}")


def _retry_digest(
    course_id: CourseId,
    session_id: SessionId,
    run_id: RunId,
    idempotency_key: str,
    purpose: str,
) -> str:
    require_text(idempotency_key, "idempotency_key")
    require_text(purpose, "purpose")
    identity = f"{course_id}\0{session_id}\0{run_id}\0{idempotency_key}\0{purpose}".encode()
    return sha256(identity).hexdigest()
