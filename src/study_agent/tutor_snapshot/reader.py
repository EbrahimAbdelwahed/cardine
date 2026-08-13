"""Single-capture composition of the public tutor snapshot."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import date
from typing import cast

from cardine.courses import ProjectionCourseView
from cardine.domain.study_context import StudyStatementValue
from study_agent.domain import (
    AnswerId,
    AnswerRecord,
    AssistantTurnRecord,
    CourseId,
    CourseProfile,
    DomainEvent,
    InteractionId,
    InteractionKind,
    InteractionRecord,
    RevisionId,
    SessionId,
    SourceId,
    StudyContextSnapshot,
    StudyStatementKind,
    TutorConfiguredHint,
    TutorConfiguredSourceField,
    TutorContextField,
    TutorContextState,
    TutorHintDivergence,
    TutorMaterialSummary,
    TutorNote,
    TutorSnapshotV1,
    TutorStatementEvidence,
    TutorTimelineEntry,
    TutorTimelineKind,
    TutorTimelineStatus,
)
from study_agent.domain._validation import JsonValue
from study_agent.ingestion import decode_source_revision_ingested
from study_agent.ports import EventStore
from study_agent.sessions import (
    SESSION_ANSWER_RECORDED,
    SESSION_ASSISTANT_TURN_RECORDED,
    SESSION_INTERACTION_RECORDED,
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    decode_answer_recorded,
    decode_assistant_turn_recorded,
    decode_interaction_recorded,
)
from study_agent.state import EventRegistry, Projection, replay
from study_agent.study_context import ProjectionStudyContextView


class TutorSnapshotReader:
    """Compose a policy-free snapshot from exactly one immutable event capture."""

    def __init__(self, events: EventStore, registry: EventRegistry) -> None:
        self._events = events
        self._registry = registry

    def get(self, course_id: CourseId, session_id: SessionId) -> TutorSnapshotV1:
        captured = tuple(self._events.read(course_id))
        projection = replay(course_id, captured, self._registry)
        load = _captured_loader(course_id, projection)

        course = ProjectionCourseView(load).get(course_id)
        session_view = ProjectionSessionView(load)
        session = session_view.get_session(course_id, session_id)
        interactions = {
            item.id: item for item in session_view.interactions(course_id, session_id)
        }
        answers = {
            item.id: item for item in session_view.answers(course_id, session_id)
        }
        assistant_turns = {
            item.id: item
            for item in ProjectionAssistantTurnView(load).turns(course_id, session_id)
        }
        context = ProjectionStudyContextView(load).get(course_id)

        configured = _configured_hints(course)
        learner_context = _learner_context(context)
        timeline = _timeline(
            captured,
            session_id,
            interactions,
            answers,
            assistant_turns,
        )
        return TutorSnapshotV1(
            course_id=course_id,
            session_id=session_id,
            high_water_sequence=projection.sequence,
            session_status=session.status,
            continuation_summary=session.continuation_summary,
            configured_hints=configured,
            learner_context=learner_context,
            divergences=_divergences(configured, learner_context),
            timeline=timeline,
            notes=tuple(
                TutorNote(
                    item.interaction_id,
                    item.content,
                    item.occurred_at,
                    item.event_id,
                    item.course_sequence,
                )
                for item in timeline
                if item.kind is TutorTimelineKind.NOTE
            ),
            materials=_materials(projection),
        )


def _captured_loader(
    course_id: CourseId, projection: Projection
) -> Callable[[CourseId], Projection]:
    def load(requested: CourseId) -> Projection:
        if requested != course_id:
            raise ValueError("captured projection belongs to another course")
        return projection

    return load


def _configured_hints(course: CourseProfile) -> tuple[TutorConfiguredHint, ...]:
    # Kept local so configured attribution cannot be confused with learner evidence.
    result: list[TutorConfiguredHint] = []
    if course.learning_goals:
        result.append(
            TutorConfiguredHint(
                StudyStatementKind.OBJECTIVE,
                course.learning_goals,
                TutorConfiguredSourceField.LEARNING_GOALS,
            )
        )
    if course.exam_date is not None:
        result.append(
            TutorConfiguredHint(
                StudyStatementKind.DEADLINE,
                (course.exam_date,),
                TutorConfiguredSourceField.EXAM_DATE,
            )
        )
    if course.assessment_styles:
        result.append(
            TutorConfiguredHint(
                StudyStatementKind.ASSESSMENT_FORMAT,
                course.assessment_styles,
                TutorConfiguredSourceField.ASSESSMENT_STYLES,
            )
        )
    return tuple(result)


def _learner_context(context: StudyContextSnapshot) -> tuple[TutorContextField, ...]:
    conflict_kinds = {item.kind for item in context.conflicts}
    result: list[TutorContextField] = []
    for kind in StudyStatementKind:
        active = context.active(kind)
        evidence = tuple(
            TutorStatementEvidence(
                item.id,
                item.session_id,
                item.origin_interaction_id,
                item.value,
                item.recorded_at,
            )
            for item in active
        )
        state = (
            TutorContextState.CONFLICTING
            if kind in conflict_kinds
            else TutorContextState.KNOWN
            if evidence
            else TutorContextState.MISSING
        )
        result.append(TutorContextField(kind, state, evidence))
    return tuple(result)


def _divergences(
    configured: tuple[TutorConfiguredHint, ...],
    learner: tuple[TutorContextField, ...],
) -> tuple[TutorHintDivergence, ...]:
    active_by_kind = {item.kind: item.active for item in learner}
    result: list[TutorHintDivergence] = []
    for item in configured:
        evidence = active_by_kind[item.kind]
        learner_values = _unique_values(tuple(entry.value for entry in evidence))
        if not learner_values or _value_keys(item.values) == _value_keys(learner_values):
            continue
        result.append(
            TutorHintDivergence(
                item.kind,
                item.values,
                learner_values,
                tuple(entry.statement_id for entry in evidence),
            )
        )
    return tuple(result)


def _unique_values(values: tuple[StudyStatementValue, ...]) -> tuple[StudyStatementValue, ...]:
    seen: set[tuple[str, str]] = set()
    result: list[StudyStatementValue] = []
    for value in values:
        key = _value_key(value)
        if key not in seen:
            seen.add(key)
            result.append(value)
    return tuple(result)


def _value_keys(values: tuple[StudyStatementValue, ...]) -> frozenset[tuple[str, str]]:
    return frozenset(_value_key(value) for value in values)


def _value_key(value: StudyStatementValue) -> tuple[str, str]:
    return type(value).__name__, value.isoformat() if isinstance(value, date) else str(value)


def _timeline(
    captured: tuple[DomainEvent, ...],
    session_id: SessionId,
    interactions: Mapping[InteractionId, InteractionRecord],
    answers: Mapping[AnswerId, AnswerRecord],
    assistant_turns: Mapping[InteractionId, AssistantTurnRecord],
) -> tuple[TutorTimelineEntry, ...]:
    result: list[TutorTimelineEntry] = []
    for event in captured:
        if event.session_id != session_id:
            continue
        if event.event_type == SESSION_INTERACTION_RECORDED:
            decoded = decode_interaction_recorded(event)
            projected_interaction = interactions.get(decoded.interaction_id)
            expected = InteractionRecord(
                decoded.interaction_id,
                decoded.kind,
                event.occurred_at,
                decoded.content,
            )
            if projected_interaction != expected:
                raise ValueError("captured interaction does not match its projection")
            kind = (
                TutorTimelineKind.LEARNER
                if decoded.kind is InteractionKind.HUMAN
                else TutorTimelineKind.NOTE
            )
            result.append(
                TutorTimelineEntry(
                    kind,
                    decoded.interaction_id,
                    event.occurred_at,
                    decoded.content,
                    event.event_id,
                    event.course_sequence,
                )
            )
        elif event.event_type == SESSION_ANSWER_RECORDED:
            answer = decode_answer_recorded(event).record
            projected_answer = answers.get(answer.id)
            if projected_answer != answer:
                raise ValueError("captured grounded answer does not match its projection")
            result.append(
                TutorTimelineEntry(
                    TutorTimelineKind.ASSISTANT,
                    answer.interaction_id,
                    event.occurred_at,
                    _grounded_content(answer),
                    event.event_id,
                    event.course_sequence,
                    answer.run_id,
                    TutorTimelineStatus(answer.answer.status.value),
                    answer.question_interaction_id,
                )
            )
        elif event.event_type == SESSION_ASSISTANT_TURN_RECORDED:
            turn = decode_assistant_turn_recorded(event).record
            projected_turn = assistant_turns.get(turn.id)
            if projected_turn != turn:
                raise ValueError("captured assistant turn does not match its projection")
            result.append(
                TutorTimelineEntry(
                    TutorTimelineKind.ASSISTANT,
                    turn.id,
                    turn.occurred_at,
                    turn.content,
                    turn.event_id,
                    turn.course_sequence,
                    turn.output.run_id,
                    TutorTimelineStatus(turn.status.value),
                    turn.in_reply_to_interaction_id,
                )
            )
    return tuple(result)


def _grounded_content(record: AnswerRecord) -> str:
    texts = tuple(segment.text for segment in record.answer.segments)
    if texts:
        return "\n\n".join(texts)
    note = record.answer.unsupported_information_note
    if note is None:
        raise ValueError("grounded answer has no canonical assistant content")
    return note


def _materials(projection: Projection) -> tuple[TutorMaterialSummary, ...]:
    sources = _mapping(projection.state.get("sources", {}), "sources")
    chunks = _mapping(projection.state.get("chunks", {}), "chunks")
    grouped_chunks: dict[tuple[str, str], list[tuple[str, Mapping[str, JsonValue]]]] = {}
    for chunk_id, raw_chunk in chunks.items():
        if (
            not isinstance(chunk_id, str)
            or not isinstance(raw_chunk, Mapping)
            or raw_chunk.get("chunk_id") != chunk_id
        ):
            raise ValueError("chunk projection entry is corrupt")
        source_id = _text(raw_chunk.get("source_id"), "chunk source_id")
        revision_id = _text(raw_chunk.get("revision_id"), "chunk revision_id")
        grouped_chunks.setdefault((source_id, revision_id), []).append(
            (chunk_id, raw_chunk)
        )
    result: list[TutorMaterialSummary] = []
    consumed_chunk_groups: set[tuple[str, str]] = set()
    for source_id, raw_source in sorted(sources.items(), key=lambda item: str(item[0])):
        if not isinstance(source_id, str) or not isinstance(raw_source, Mapping):
            raise ValueError("source projection entry is corrupt")
        if set(raw_source) != {"revision_ids", "revisions", "current_revision_id"}:
            raise ValueError("source projection fields are corrupt")
        current = _text(raw_source.get("current_revision_id"), "current_revision_id")
        revisions = _mapping(raw_source.get("revisions"), "revisions")
        revision_ids_raw = raw_source.get("revision_ids")
        if not isinstance(revision_ids_raw, tuple) or any(
            not isinstance(item, str) for item in revision_ids_raw
        ):
            raise ValueError("source revision_ids are corrupt")
        revision_ids = cast(tuple[str, ...], revision_ids_raw)
        if (
            len(set(revision_ids)) != len(revision_ids)
            or set(revisions) != set(revision_ids)
            or current not in revisions
        ):
            raise ValueError("source revision history is corrupt")
        current_revision = None
        for revision_id in revision_ids:
            raw_revision = revisions.get(revision_id)
            if not isinstance(raw_revision, Mapping) or set(raw_revision) != {
                "source",
                "normalized_character_length",
                "chunking",
            }:
                raise ValueError("source revision projection is corrupt")
            group_key = (source_id, revision_id)
            revision_chunks = grouped_chunks.get(group_key, [])
            try:
                ordered_chunks = tuple(
                    raw
                    for _, raw in sorted(
                        revision_chunks,
                        key=lambda item: _ordinal(item[1].get("ordinal")),
                    )
                )
                decoded = decode_source_revision_ingested(
                    {
                        "source": raw_revision["source"],
                        "chunks": ordered_chunks,
                        "normalized_character_length": raw_revision[
                            "normalized_character_length"
                        ],
                        "chunking": raw_revision["chunking"],
                    }
                )
            except (KeyError, TypeError, ValueError) as error:
                raise ValueError("source revision projection is corrupt") from error
            if (
                str(decoded.source.source_id) != source_id
                or str(decoded.source.revision_id) != revision_id
            ):
                raise ValueError("source revision ownership is corrupt")
            consumed_chunk_groups.add(group_key)
            if revision_id == current:
                current_revision = decoded
        if current_revision is None:  # pragma: no cover - guarded above
            raise ValueError("current source revision is missing")
        source = current_revision.source
        result.append(
            TutorMaterialSummary(
                SourceId(source_id),
                RevisionId(current),
                source.title,
                source.kind,
                source.checksum_sha256,
                source.source_role,
                source.trust_level,
                len(current_revision.chunks),
            )
        )
    if set(grouped_chunks) != consumed_chunk_groups:
        raise ValueError("orphan source chunks exist in the captured projection")
    return tuple(result)


def _ordinal(value: JsonValue | None) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ValueError("chunk ordinal is corrupt")
    return value


def _mapping(value: JsonValue | None, name: str) -> Mapping[str, JsonValue]:
    if not isinstance(value, Mapping):
        raise ValueError(f"projection field {name} must be an object")
    return value


def _text(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise ValueError(f"projection field {name} must be non-empty text")
    return value


__all__ = ["TutorSnapshotReader"]
