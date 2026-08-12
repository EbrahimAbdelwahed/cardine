"""Storage-neutral assembly of the deterministic public export v1."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum

from study_agent.artifacts import (
    ARTIFACT_EVENT_TYPES,
    ProjectionArtifactView,
    register_artifact_events,
)
from study_agent.assessments import ASSESSMENT_EVENT_TYPES, register_assessment_events
from cardine.courses import register_course_events
from cardine.courses.events import COURSE_CREATED, decode_course_created
from study_agent.domain._validation import JsonObject, freeze_object
from cardine.domain.course import CourseProfile
from study_agent.domain.events import Actor, DomainEvent, PrincipalKind
from study_agent.domain.grounding import GroundedAnswer
from study_agent.domain.identifiers import CorrelationId, CourseId, EventId
from study_agent.domain.session import (
    AnswerRecord,
    ContinuationSummaryV1,
    InteractionKind,
    InteractionRecord,
    StudySessionRecord,
)
from study_agent.domain.source import Citation, SourceChunk
from study_agent.ingestion import (
    SOURCE_REVISION_SELECTED,
    SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
    decode_source_revision_selected_event,
    reduce_source_revision,
    reduce_source_revision_selected,
)
from study_agent.ingestion.events import (
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    SourceRevisionIngested,
    decode_source_revision_ingested,
)
from study_agent.ingestion.identity import source_event_id_for
from study_agent.ports import EventStore
from study_agent.recall.contracts import AppliedSchedule, ReviewRecord
from study_agent.recall.events import (
    RECALL_EVENT_TYPES,
)
from study_agent.recall.projection import register_recall_events
from study_agent.recall.view import ProjectionRecallView
from study_agent.sessions import (
    SESSION_EVENT_TYPES,
    ProjectionSessionView,
    register_session_events,
)
from study_agent.sessions.events import (
    SESSION_ANSWER_RECORDED,
    SESSION_ASSISTANT_TURN_RECORDED,
    SESSION_CONTINUATION_SUMMARY_UPDATED,
    SESSION_ENDED,
    SESSION_INTERACTION_RECORDED,
    SESSION_RESUMED,
    SESSION_STARTED,
    SESSION_SUSPENDED,
    decode_answer_recorded,
    decode_assistant_turn_recorded,
    decode_interaction_recorded,
    decode_lifecycle,
    decode_session_started,
    decode_summary_updated,
)
from study_agent.state import EventRegistry, Projection
from study_agent.study_context import (
    CONFLICT_RESOLVED,
    STATEMENT_RECORDED,
    STATEMENT_RETRACTED,
    STUDY_CONTEXT_EVENT_TYPES,
    ProjectionStudyContextView,
    decode_conflict_resolved,
    decode_statement_recorded,
    decode_statement_retracted,
    register_study_context_events,
)

EXPORT_SCHEMA_VERSION = 1
EXPORT_V2_SCHEMA_VERSION = 2
EXPORT_V3_SCHEMA_VERSION = 3


class ExportVersion(StrEnum):
    V1 = "1"
    V2 = "2"
    V3 = "3"


class ExportStateError(ValueError):
    """Canonical state cannot be represented by the exact export-v1 contract."""


@dataclass(frozen=True, slots=True)
class ExportBundle:
    """Immutable allowlisted records ready for an export adapter."""

    course_id: CourseId
    high_water_sequence: int
    course: JsonObject
    sources: tuple[JsonObject, ...]
    sessions: tuple[JsonObject, ...]
    answers: tuple[JsonObject, ...]
    events: tuple[JsonObject, ...]

    def __post_init__(self) -> None:
        if type(self.course_id) is not CourseId:
            raise TypeError("course_id must be a CourseId")
        if type(self.high_water_sequence) is not int or self.high_water_sequence < 1:
            raise ValueError("high_water_sequence must be a positive integer")
        object.__setattr__(self, "course", freeze_object(self.course))
        for name in ("sources", "sessions", "answers", "events"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise TypeError(f"{name} must be a tuple")
            object.__setattr__(self, name, tuple(freeze_object(item) for item in values))


@dataclass(frozen=True, slots=True)
class ExportBundleV2:
    """Immutable allowlisted records for explicit export v2."""

    course_id: CourseId
    high_water_sequence: int
    course: JsonObject
    sources: tuple[JsonObject, ...]
    sessions: tuple[JsonObject, ...]
    answers: tuple[JsonObject, ...]
    events: tuple[JsonObject, ...]
    artifacts: tuple[JsonObject, ...]

    def __post_init__(self) -> None:
        if type(self.course_id) is not CourseId:
            raise TypeError("course_id must be a CourseId")
        if type(self.high_water_sequence) is not int or self.high_water_sequence < 1:
            raise ValueError("high_water_sequence must be a positive integer")
        object.__setattr__(self, "course", freeze_object(self.course))
        for name in ("sources", "sessions", "answers", "events", "artifacts"):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise TypeError(f"{name} must be a tuple")
            object.__setattr__(self, name, tuple(freeze_object(item) for item in values))


@dataclass(frozen=True, slots=True)
class ExportBundleV3:
    """Immutable allowlisted records for explicit export v3.

    Recall is represented by one typed receipt stream.  Package scheduler
    objects, raw policy configuration, retry identities, and adapter-owned
    state never enter this DTO.
    """

    course_id: CourseId
    high_water_sequence: int
    course: JsonObject
    sources: tuple[JsonObject, ...]
    sessions: tuple[JsonObject, ...]
    answers: tuple[JsonObject, ...]
    events: tuple[JsonObject, ...]
    artifacts: tuple[JsonObject, ...]
    recall: tuple[JsonObject, ...]

    def __post_init__(self) -> None:
        if type(self.course_id) is not CourseId:
            raise TypeError("course_id must be a CourseId")
        if type(self.high_water_sequence) is not int or self.high_water_sequence < 1:
            raise ValueError("high_water_sequence must be a positive integer")
        object.__setattr__(self, "course", freeze_object(self.course))
        for name in (
            "sources",
            "sessions",
            "answers",
            "events",
            "artifacts",
            "recall",
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple):
                raise TypeError(f"{name} must be a tuple")
            object.__setattr__(self, name, tuple(freeze_object(item) for item in values))


class ExportService:
    """Build an export solely from canonical events and projection read ports."""

    def __init__(
        self,
        events: EventStore,
    ) -> None:
        self._events = events

    def assemble(
        self, course_id: CourseId, *, version: ExportVersion = ExportVersion.V1
    ) -> ExportBundle | ExportBundleV2 | ExportBundleV3:
        if not isinstance(version, ExportVersion):
            raise TypeError("version must be an ExportVersion")
        stream = tuple(self._events.read(course_id))
        if version is ExportVersion.V2:
            return self._assemble_v2(course_id, stream)
        if version is ExportVersion.V3:
            return self._assemble_v3(course_id, stream)
        _reject_v1_artifact_stream(stream)
        _validate_stream(course_id, stream)

        created = decode_course_created(stream[0])

        revisions: list[SourceRevisionIngested] = []
        for event in stream:
            decoded = _decode_allowlisted_event(event)
            if isinstance(decoded, SourceRevisionIngested):
                revisions.append(decoded)
        revision_keys = tuple(
            (item.source.source_id, item.source.revision_id) for item in revisions
        )
        if len(set(revision_keys)) != len(revision_keys):
            raise ExportStateError("event stream contains duplicate source revisions")

        contextual_projection = _replay_contextual_state(course_id, stream)
        ProjectionStudyContextView(
            lambda requested: _owned_projection(requested, contextual_projection)
        ).get(course_id)
        session_view = ProjectionSessionView(
            lambda requested: _owned_projection(requested, contextual_projection)
        )
        session_records = session_view.list_sessions(course_id)

        answers: list[JsonObject] = []
        sessions: list[JsonObject] = []
        domain_answers: list[AnswerRecord] = []
        for record in session_records:
            interactions = session_view.interactions(course_id, record.id)
            session_answers = session_view.answers(course_id, record.id)
            sessions.append(_session_record(record, interactions))
            answers.extend(_answer_record(record, answer) for answer in session_answers)
            domain_answers.extend(session_answers)
        _validate_source_references(revisions, domain_answers)

        return ExportBundle(
            course_id=course_id,
            high_water_sequence=stream[-1].course_sequence,
            course=_course_record(created.profile),
            sources=tuple(
                _source_record(item)
                for item in sorted(
                    revisions,
                    key=lambda item: (str(item.source.source_id), str(item.source.revision_id)),
                )
            ),
            sessions=tuple(sessions),
            answers=tuple(
                sorted(
                    answers,
                    key=lambda item: (str(item["session_id"]), str(item["answer_id"])),
                )
            ),
            events=tuple(_event_record(event) for event in stream),
        )

    def _assemble_v2(self, course_id: CourseId, stream: tuple[DomainEvent, ...]) -> ExportBundleV2:
        _reject_recall_stream(stream)
        projection = _replay_v2(course_id, stream)
        created = decode_course_created(stream[0])
        revisions = tuple(
            _decode_source_event(event)
            for event in stream
            if event.event_type == SOURCE_REVISION_INGESTED
        )
        revision_keys = tuple(
            (item.source.source_id, item.source.revision_id) for item in revisions
        )
        if len(set(revision_keys)) != len(revision_keys):
            raise ExportStateError("event stream contains duplicate source revisions")

        def context_loader(requested: CourseId) -> Projection:
            return _owned_projection(requested, projection)

        ProjectionStudyContextView(context_loader).get(course_id)
        session_view = ProjectionSessionView(context_loader)
        session_records = session_view.list_sessions(course_id)
        answers: list[JsonObject] = []
        sessions: list[JsonObject] = []
        domain_answers: list[AnswerRecord] = []
        for record in session_records:
            interactions = session_view.interactions(course_id, record.id)
            session_answers = session_view.answers(course_id, record.id)
            sessions.append(_session_record_v2(record, interactions))
            answers.extend(
                _with_schema(_answer_record(record, item), 2) for item in session_answers
            )
            domain_answers.extend(session_answers)
        _validate_source_references(revisions, domain_answers)
        try:
            snapshot = ProjectionArtifactView(
                lambda requested: _owned_projection(requested, projection)
            ).get(course_id)
            from .artifact_export import artifact_rows

            artifacts = artifact_rows(stream, snapshot, revisions)
        except (TypeError, ValueError, LookupError) as error:
            raise ExportStateError("artifact events cannot be exported canonically") from error
        return ExportBundleV2(
            course_id,
            stream[-1].course_sequence,
            _with_schema(_course_record(created.profile), 2),
            tuple(
                _with_schema(_source_record(item), 2)
                for item in sorted(
                    revisions,
                    key=lambda item: (str(item.source.source_id), str(item.source.revision_id)),
                )
            ),
            tuple(sessions),
            tuple(
                sorted(
                    answers,
                    key=lambda item: (str(item["session_id"]), str(item["answer_id"])),
                )
            ),
            tuple(_event_record(event) for event in stream),
            artifacts,
        )

    def _assemble_v3(self, course_id: CourseId, stream: tuple[DomainEvent, ...]) -> ExportBundleV3:
        projection = _replay_v3(course_id, stream)
        created = decode_course_created(stream[0])
        revisions = tuple(
            _decode_source_event(event)
            for event in stream
            if event.event_type == SOURCE_REVISION_INGESTED
        )
        revision_keys = tuple(
            (item.source.source_id, item.source.revision_id) for item in revisions
        )
        if len(set(revision_keys)) != len(revision_keys):
            raise ExportStateError("event stream contains duplicate source revisions")

        def context_loader(requested: CourseId) -> Projection:
            return _owned_projection(requested, projection)

        ProjectionStudyContextView(context_loader).get(course_id)
        session_view = ProjectionSessionView(context_loader)
        session_records = session_view.list_sessions(course_id)
        answers: list[JsonObject] = []
        sessions: list[JsonObject] = []
        domain_answers: list[AnswerRecord] = []
        for record in session_records:
            interactions = session_view.interactions(course_id, record.id)
            session_answers = session_view.answers(course_id, record.id)
            sessions.append(_session_record_v2(record, interactions))
            answers.extend(
                _with_schema(_answer_record(record, item), EXPORT_V3_SCHEMA_VERSION)
                for item in session_answers
            )
            domain_answers.extend(session_answers)
        _validate_source_references(revisions, domain_answers)
        try:
            snapshot = ProjectionArtifactView(context_loader).get(course_id)
            from .artifact_export import artifact_rows

            artifacts = artifact_rows(stream, snapshot, revisions)
        except (TypeError, ValueError, LookupError) as error:
            raise ExportStateError("artifact events cannot be exported canonically") from error
        recall = _recall_rows(projection)
        return ExportBundleV3(
            course_id,
            stream[-1].course_sequence,
            _with_schema(_course_record(created.profile), EXPORT_V3_SCHEMA_VERSION),
            tuple(
                _with_schema(_source_record(item), EXPORT_V3_SCHEMA_VERSION)
                for item in sorted(
                    revisions,
                    key=lambda item: (str(item.source.source_id), str(item.source.revision_id)),
                )
            ),
            tuple(sessions),
            tuple(
                sorted(
                    answers,
                    key=lambda item: (str(item["session_id"]), str(item["answer_id"])),
                )
            ),
            tuple(_event_record(event) for event in stream),
            tuple(artifacts),
            recall,
        )


type _EventDecoder = Callable[[DomainEvent], object]


def _reject_v1_artifact_stream(stream: Sequence[DomainEvent]) -> None:
    _reject_recall_stream(stream)
    if any(event.event_type in ARTIFACT_EVENT_TYPES | ASSESSMENT_EVENT_TYPES for event in stream):
        raise ExportStateError("artifact export requires v2")


def _reject_recall_stream(stream: Sequence[DomainEvent]) -> None:
    if any(event.event_type in RECALL_EVENT_TYPES for event in stream):
        raise ExportStateError("recall export requires v3")


def _decode_allowlisted_event(event: DomainEvent) -> object:
    if event.event_type in ARTIFACT_EVENT_TYPES:
        raise ExportStateError("artifact export requires v2")
    decoders: Mapping[str, _EventDecoder] = {
        "course.created": decode_course_created,
        SOURCE_REVISION_INGESTED: _decode_source_event,
        SESSION_STARTED: decode_session_started,
        SESSION_INTERACTION_RECORDED: decode_interaction_recorded,
        SESSION_ANSWER_RECORDED: decode_answer_recorded,
        SESSION_ASSISTANT_TURN_RECORDED: decode_assistant_turn_recorded,
        SESSION_CONTINUATION_SUMMARY_UPDATED: decode_summary_updated,
        SESSION_SUSPENDED: lambda value: decode_lifecycle(value, SESSION_SUSPENDED),
        SESSION_RESUMED: lambda value: decode_lifecycle(value, SESSION_RESUMED),
        SESSION_ENDED: lambda value: decode_lifecycle(value, SESSION_ENDED),
        STATEMENT_RECORDED: decode_statement_recorded,
        STATEMENT_RETRACTED: decode_statement_retracted,
        CONFLICT_RESOLVED: decode_conflict_resolved,
    }
    try:
        decoder = decoders[event.event_type]
    except KeyError as error:
        raise ExportStateError(
            f"event schema is not allowlisted for export: {event.event_type}@{event.schema_version}"
        ) from error
    try:
        return decoder(event)
    except (TypeError, ValueError) as error:
        raise ExportStateError(
            f"invalid canonical event: {event.event_type}@{event.schema_version}"
        ) from error


def _decode_source_event(event: DomainEvent) -> SourceRevisionIngested:
    if (
        event.event_type != SOURCE_REVISION_INGESTED
        or event.schema_version != SOURCE_REVISION_SCHEMA_VERSION
    ):
        raise ValueError("event envelope does not match source.revision_ingested@1")
    if event.session_id is not None or event.causation_id is not None:
        raise ValueError("source ingestion cannot be session-scoped or caused")
    if not isinstance(event.event_id, EventId):
        raise ValueError("source event id envelope is not typed")
    if not isinstance(event.correlation_id, CorrelationId):
        raise ValueError("source correlation envelope is not typed")
    if (
        not isinstance(event.actor, Actor)
        or not isinstance(event.actor.kind, PrincipalKind)
        or event.actor.kind not in (PrincipalKind.HUMAN, PrincipalKind.SERVICE)
    ):
        raise ValueError("source ingestion requires a trusted actor")
    decoded = decode_source_revision_ingested(event.payload)
    if event.event_id != source_event_id_for(event.course_id, decoded.source.revision_id):
        raise ValueError("source event id does not match its canonical revision")
    return decoded


def _validate_stream(course_id: CourseId, stream: Sequence[DomainEvent]) -> None:
    if type(course_id) is not CourseId:
        raise TypeError("course_id must be a CourseId")
    if not stream:
        raise ExportStateError("course event stream is empty")
    for expected_sequence, event in enumerate(stream, start=1):
        if event.course_id != course_id:
            raise ExportStateError("event stream contains another course")
        if event.course_sequence != expected_sequence:
            raise ExportStateError("event stream sequence is not contiguous")
        _decode_allowlisted_event(event)


def _replay_contextual_state(course_id: CourseId, stream: Sequence[DomainEvent]) -> Projection:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    register_study_context_events(registry)
    state: JsonObject = {}
    contextual_types = frozenset({COURSE_CREATED}) | SESSION_EVENT_TYPES | STUDY_CONTEXT_EVENT_TYPES
    try:
        for event in stream:
            if event.event_type in contextual_types:
                state = registry.reduce(state, event)
    except (TypeError, ValueError) as error:
        raise ExportStateError("contextual events cannot be replayed canonically") from error
    return Projection(course_id, stream[-1].course_sequence, state)


def _replay_v2(course_id: CourseId, stream: Sequence[DomainEvent]) -> Projection:
    _reject_recall_stream(stream)
    if type(course_id) is not CourseId:
        raise TypeError("course_id must be a CourseId")
    if not stream:
        raise ExportStateError("course event stream is empty")
    registry = EventRegistry()
    register_course_events(registry)
    registry.register_event(
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SCHEMA_VERSION,
        _decode_source_event,
        reduce_source_revision,
    )
    registry.register_event(
        SOURCE_REVISION_SELECTED,
        SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
        decode_source_revision_selected_event,
        reduce_source_revision_selected,
    )
    register_session_events(registry)
    register_study_context_events(registry)
    register_artifact_events(registry)
    register_assessment_events(registry)
    state: JsonObject = {}
    try:
        for expected_sequence, event in enumerate(stream, start=1):
            if event.course_id != course_id:
                raise ExportStateError("event stream contains another course")
            if event.course_sequence != expected_sequence:
                raise ExportStateError("event stream sequence is not contiguous")
            state = registry.reduce(state, event)
    except ExportStateError:
        raise
    except (TypeError, ValueError, LookupError) as error:
        raise ExportStateError("event stream cannot be replayed for export v2") from error
    return Projection(course_id, stream[-1].course_sequence, state)


def _replay_v3(course_id: CourseId, stream: Sequence[DomainEvent]) -> Projection:
    if type(course_id) is not CourseId:
        raise TypeError("course_id must be a CourseId")
    if not stream:
        raise ExportStateError("course event stream is empty")
    registry = EventRegistry()
    register_course_events(registry)
    registry.register_event(
        SOURCE_REVISION_INGESTED,
        SOURCE_REVISION_SCHEMA_VERSION,
        _decode_source_event,
        reduce_source_revision,
    )
    registry.register_event(
        SOURCE_REVISION_SELECTED,
        SOURCE_REVISION_SELECTED_SCHEMA_VERSION,
        decode_source_revision_selected_event,
        reduce_source_revision_selected,
    )
    register_session_events(registry)
    register_study_context_events(registry)
    register_artifact_events(registry)
    register_assessment_events(registry)
    register_recall_events(registry)
    state: JsonObject = {}
    try:
        for expected_sequence, event in enumerate(stream, start=1):
            if event.course_id != course_id:
                raise ExportStateError("event stream contains another course")
            if event.course_sequence != expected_sequence:
                raise ExportStateError("event stream sequence is not contiguous")
            state = registry.reduce(state, event)
    except ExportStateError:
        raise
    except (TypeError, ValueError, LookupError) as error:
        raise ExportStateError("event stream cannot be replayed for export v3") from error
    return Projection(course_id, stream[-1].course_sequence, state)


def _recall_rows(projection: Projection) -> tuple[JsonObject, ...]:
    """Render the exact v3 recall receipt allowlist from one replayed projection."""

    if "recall" not in projection.state:
        return ()
    raw = projection.state["recall"]
    if not isinstance(raw, Mapping):
        raise ExportStateError("recall projection cannot be exported canonically")
    schedules = raw.get("schedules", {})
    reviews = raw.get("reviews", {})
    if not isinstance(schedules, Mapping) or not isinstance(reviews, Mapping):
        raise ExportStateError("recall projection cannot be exported canonically")
    try:
        snapshot = ProjectionRecallView(lambda _: projection).get(projection.course_id)
    except (TypeError, ValueError, LookupError) as error:
        raise ExportStateError("recall projection cannot be exported canonically") from error

    schedule_receipts: list[JsonObject] = []
    for schedule in snapshot.schedules:
        source = schedules.get(str(schedule.decision_id))
        if not isinstance(source, Mapping):
            raise ExportStateError("recall schedule receipt is missing its event scope")
        schedule_receipts.append(_schedule_receipt(schedule, source))
    review_receipts: list[JsonObject] = []
    for review in snapshot.reviews:
        source = reviews.get(str(review.review_id))
        if not isinstance(source, Mapping):
            raise ExportStateError("recall review receipt is missing its event scope")
        review_receipts.append(_review_receipt(review, source))
    return tuple(
        sorted(
            (*review_receipts, *schedule_receipts),
            key=lambda row: (
                _receipt_sequence(row),
                str(row["receipt_type"]),
                str(row.get("review_id", row.get("decision_id", ""))),
            ),
        )
    )


def _receipt_sequence(row: Mapping[str, object]) -> int:
    sequence = row.get("course_sequence")
    if type(sequence) is not int:
        raise ExportStateError("recall receipt course sequence is invalid")
    return sequence


def _review_receipt(review: ReviewRecord, source: Mapping[str, object]) -> JsonObject:
    sequence = source.get("course_sequence")
    session_id = source.get("session_id")
    if type(sequence) is not int or not isinstance(session_id, str):
        raise ExportStateError("recall review receipt scope is invalid")
    return {
        "schema_version": EXPORT_V3_SCHEMA_VERSION,
        "receipt_type": "review",
        "course_sequence": sequence,
        "session_id": session_id,
        "review_id": str(review.review_id),
        "revision_id": str(review.revision_id),
        "rating": review.rating.value,
        "latency_ms": review.latency_ms,
        "confidence_bps": review.confidence_bps,
        "occurred_at": review.occurred_at.isoformat().replace("+00:00", "Z"),
    }


def _schedule_receipt(
    schedule: AppliedSchedule, source: Mapping[str, object]
) -> JsonObject:
    sequence = source.get("course_sequence")
    session_id = source.get("session_id")
    if type(sequence) is not int or not isinstance(session_id, str):
        raise ExportStateError("recall schedule receipt scope is invalid")
    return {
        "schema_version": EXPORT_V3_SCHEMA_VERSION,
        "receipt_type": "schedule",
        "course_sequence": sequence,
        "session_id": session_id,
        "decision_id": str(schedule.decision_id),
        "revision_id": str(schedule.revision_id),
        "trigger": schedule.trigger,
        "review_id": str(schedule.review_id) if schedule.review_id is not None else None,
        "enrollment_at": schedule.enrollment_at.isoformat().replace("+00:00", "Z"),
        "due_at": schedule.due_at.isoformat().replace("+00:00", "Z"),
        "policy_id": schedule.policy_id,
        "policy_version": schedule.policy_version,
        "policy_fingerprint": schedule.policy_fingerprint,
        "implementation_id": schedule.implementation_id,
        "implementation_version": schedule.implementation_version,
        "history_fingerprint": schedule.history_fingerprint,
        "result_fingerprint": schedule.result_fingerprint,
    }


def _owned_projection(requested: CourseId, projection: Projection) -> Projection:
    if requested != projection.course_id:
        raise ExportStateError("session replay requested another course")
    return projection


def _validate_source_references(
    revisions: Sequence[SourceRevisionIngested], answers: Sequence[AnswerRecord]
) -> None:
    chunks: dict[tuple[str, str, str], SourceChunk] = {}
    for revision in revisions:
        for chunk in revision.chunks:
            key = (str(chunk.source_id), str(chunk.revision_id), str(chunk.chunk_id))
            if key in chunks:
                raise ExportStateError("exported source revisions contain a duplicate chunk")
            chunks[key] = chunk
    for record in answers:
        for segment in record.answer.segments:
            for citation in segment.citations:
                _validate_span_reference(
                    chunks,
                    str(citation.source_id),
                    str(citation.revision_id),
                    str(citation.chunk_id),
                    citation.start_offset,
                    citation.end_offset,
                )
        for commitment in record.answer.provenance.source_commitments:
            _validate_span_reference(
                chunks,
                str(commitment.source_id),
                str(commitment.revision_id),
                str(commitment.chunk_id),
                commitment.start_offset,
                commitment.end_offset,
            )


def _validate_span_reference(
    chunks: Mapping[tuple[str, str, str], SourceChunk],
    source_id: str,
    revision_id: str,
    chunk_id: str,
    start_offset: int,
    end_offset: int,
) -> None:
    try:
        chunk = chunks[(source_id, revision_id, chunk_id)]
    except KeyError as error:
        raise ExportStateError("answer references a source chunk absent from the export") from error
    if start_offset < chunk.start_offset or end_offset > chunk.end_offset:
        raise ExportStateError("answer source span exceeds its exported chunk")


def _course_record(profile: CourseProfile) -> JsonObject:
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "course_id": str(profile.id),
        "title": profile.title,
        "language": profile.language,
        "exam_date": profile.exam_date.isoformat() if profile.exam_date is not None else None,
        "assessment_styles": profile.assessment_styles,
        "learning_goals": profile.learning_goals,
        "source_policy": {
            "allowed_roles": profile.source_policy.allowed_roles,
            "minimum_trust_level": profile.source_policy.minimum_trust_level,
        },
        "terminology_policy": {
            "entries": tuple(
                {"concept": item.concept, "preferred_term": item.preferred_term}
                for item in profile.terminology_policy.entries
            )
        },
    }


def _source_record(revision: SourceRevisionIngested) -> JsonObject:
    source = revision.source
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "source_id": str(source.source_id),
        "revision_id": str(source.revision_id),
        "kind": source.kind.value,
        "title": source.title,
        "media_type": source.media_type,
        "checksum_sha256": source.checksum_sha256,
        "byte_length": source.byte_length,
        "trust_level": source.trust_level,
        "source_role": source.source_role,
        "normalization_version": source.normalization_version,
        "normalized_character_length": source.normalized_character_length,
        "structure_origin": source.structure_origin.value,
        "ingestion_method": source.ingestion_method,
        "content_origin": source.content_origin.value,
        "chunking": {
            "version": revision.chunking.version,
            "max_characters": revision.chunking.max_characters,
        },
        "chunks": tuple(_chunk_record(chunk) for chunk in revision.chunks),
    }


def _chunk_record(chunk: SourceChunk) -> JsonObject:
    return {
        "chunk_id": str(chunk.chunk_id),
        "start_offset": chunk.start_offset,
        "end_offset": chunk.end_offset,
        "section_path": chunk.section_path,
        "ordinal": chunk.ordinal,
        "checksum_sha256": chunk.checksum_sha256,
        "chunker_version": chunk.chunker_version,
    }


def _session_record(
    session: StudySessionRecord, interactions: tuple[InteractionRecord, ...]
) -> JsonObject:
    if tuple(item.id for item in interactions) != session.interaction_ids:
        raise ExportStateError("session interaction view does not match canonical order")
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "course_id": str(session.course_id),
        "session_id": str(session.id),
        "status": session.status.value,
        "run_ids": tuple(str(item) for item in session.run_ids),
        "interactions": tuple(_interaction_record(item) for item in interactions),
        "continuation_summary": (
            None
            if session.continuation_summary is None
            else _summary_record(session.continuation_summary)
        ),
    }


def _session_record_v2(
    session: StudySessionRecord, interactions: tuple[InteractionRecord, ...]
) -> JsonObject:
    """Render session linkage without exporting learner or model transcript text."""
    if tuple(item.id for item in interactions) != session.interaction_ids:
        raise ExportStateError("session interaction view does not match canonical order")
    return {
        "schema_version": EXPORT_V2_SCHEMA_VERSION,
        "course_id": str(session.course_id),
        "session_id": str(session.id),
        "status": session.status.value,
        "run_ids": tuple(str(item) for item in session.run_ids),
        "interactions": tuple(_interaction_record_v2(item) for item in interactions),
        "continuation_summary": (
            None
            if session.continuation_summary is None
            else _summary_record_v2(session.continuation_summary)
        ),
    }


def _interaction_record(interaction: InteractionRecord) -> JsonObject:
    return {
        "interaction_id": str(interaction.id),
        "kind": interaction.kind.value,
        "content": interaction.content,
        "answer_id": str(interaction.answer_id) if interaction.answer_id is not None else None,
        "run_id": str(interaction.run_id) if interaction.run_id is not None else None,
    }


def _interaction_record_v2(interaction: InteractionRecord) -> JsonObject:
    if not isinstance(interaction.kind, InteractionKind):
        raise ExportStateError("session interaction kind is not typed")
    return {
        "interaction_id": str(interaction.id),
        "kind": interaction.kind.value,
        "answer_id": str(interaction.answer_id) if interaction.answer_id is not None else None,
        "run_id": str(interaction.run_id) if interaction.run_id is not None else None,
    }


def _summary_record(summary: ContinuationSummaryV1) -> JsonObject:
    return {
        "schema_version": summary.schema_version,
        "through_interaction_id": str(summary.through_interaction_id),
        "interaction_count": summary.interaction_count,
        "recent_exchanges": tuple(
            {
                "question_interaction_id": str(item.question_interaction_id),
                "answer_interaction_id": str(item.answer_interaction_id),
                "learner_excerpt": item.learner_excerpt,
                "assistant_excerpt": item.assistant_excerpt,
                "answer_status": item.answer_status.value,
                "unsupported_note": item.unsupported_note,
            }
            for item in summary.recent_exchanges
        ),
        "grounded_points": summary.grounded_points,
        "unresolved_notes": summary.unresolved_notes,
        "character_count": summary.character_count,
    }


def _summary_record_v2(summary: ContinuationSummaryV1) -> JsonObject:
    """Expose only the typed continuation boundary, never transcript excerpts."""
    return {
        "schema_version": summary.schema_version,
        "through_interaction_id": str(summary.through_interaction_id),
        "interaction_count": summary.interaction_count,
    }


def _answer_record(session: StudySessionRecord, record: AnswerRecord) -> JsonObject:
    if record.run_id not in session.run_ids:
        raise ExportStateError("session answer is not linked to a canonical run")
    return {
        "schema_version": EXPORT_SCHEMA_VERSION,
        "course_id": str(session.course_id),
        "session_id": str(session.id),
        "answer_id": str(record.id),
        "interaction_id": str(record.interaction_id),
        "question_interaction_id": str(record.question_interaction_id),
        "run_id": str(record.run_id),
        "answer": _answer(record.answer),
    }


def _answer(answer: GroundedAnswer) -> JsonObject:
    provenance = answer.provenance
    return {
        "status": answer.status.value,
        "segments": tuple(
            {
                "kind": segment.kind.value,
                "text": segment.text,
                "claim_origin": segment.claim_origin.value,
                "citations": tuple(_citation(item) for item in segment.citations),
            }
            for segment in answer.segments
        ),
        "unsupported_information_note": answer.unsupported_information_note,
        "provenance": {
            "source_commitments": tuple(
                {
                    "source_id": str(item.source_id),
                    "revision_id": str(item.revision_id),
                    "chunk_id": str(item.chunk_id),
                    "start_offset": item.start_offset,
                    "end_offset": item.end_offset,
                }
                for item in provenance.source_commitments
            ),
            "prompt": {
                "prompt_id": provenance.prompt.prompt_id,
                "version": provenance.prompt.version,
                "composition_fingerprint": provenance.prompt.composition_fingerprint,
                "layer_fingerprints": provenance.prompt.layer_fingerprints,
            },
            "retrieval": {
                "strategy_id": provenance.retrieval.strategy_id,
                "strategy_version": provenance.retrieval.strategy_version,
                "query_fingerprint": provenance.retrieval.query_fingerprint,
                "index_version": provenance.retrieval.index_version,
                "read_set_fingerprint": provenance.retrieval.read_set_fingerprint,
            },
            "validators": tuple(
                {
                    "validator_id": item.validator_id,
                    "version": item.version,
                    "passed": item.passed,
                    "disposition": item.disposition,
                    "result_fingerprint": item.result_fingerprint,
                }
                for item in provenance.validators
            ),
            "pins": {
                "skill": provenance.pins.skill,
                "playbook": provenance.pins.playbook,
                "prompt": provenance.pins.prompt,
                "state_contract": provenance.pins.state_contract,
                "tool_behavior": provenance.pins.tool_behavior,
            },
            "playbook_run_id": str(provenance.playbook_run_id),
            "event_schema_version": provenance.event_schema_version,
            "reducer_schema_version": provenance.reducer_schema_version,
        },
    }


def _citation(citation: Citation) -> JsonObject:
    # quoted_snippet is intentionally absent: it is verbatim source content.
    return {
        "source_id": str(citation.source_id),
        "revision_id": str(citation.revision_id),
        "chunk_id": str(citation.chunk_id),
        "start_offset": citation.start_offset,
        "end_offset": citation.end_offset,
        "locator": citation.locator,
    }


def _event_record(event: DomainEvent) -> JsonObject:
    # Payload, principal id and timestamp are deliberately not part of public audit export.
    return {
        "event_id": str(event.event_id),
        "course_id": str(event.course_id),
        "course_sequence": event.course_sequence,
        "event_type": event.event_type,
        "schema_version": event.schema_version,
        "actor_kind": event.actor.kind.value,
        "correlation_id": str(event.correlation_id),
        "session_id": str(event.session_id) if event.session_id is not None else None,
        "causation_id": str(event.causation_id) if event.causation_id is not None else None,
    }


def _with_schema(value: JsonObject, version: int) -> JsonObject:
    return {**value, "schema_version": version}
