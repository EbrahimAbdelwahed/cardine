"""Typed, policy-free values for one sequence-consistent tutor snapshot."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime
from enum import StrEnum

from ._validation import JsonObject, JsonValue, require_aware, require_text
from .identifiers import (
    CourseId,
    EventId,
    InteractionId,
    RevisionId,
    RunId,
    SessionId,
    SourceId,
    StatementId,
)
from .session import ContinuationSummaryV1, SessionStatus
from .source import SourceKind
from cardine.domain.study_context import (
    StudyStatementInput,
    StudyStatementKind,
    StudyStatementValue,
)

TUTOR_SNAPSHOT_SCHEMA_VERSION = 1


class TutorContextState(StrEnum):
    MISSING = "missing"
    KNOWN = "known"
    CONFLICTING = "conflicting"


class TutorTimelineKind(StrEnum):
    LEARNER = "learner"
    NOTE = "note"
    ASSISTANT = "assistant"


class TutorTimelineStatus(StrEnum):
    ANSWERED = "answered"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    COMPLETED = "completed"
    TERMINATED = "terminated"


class TutorConfiguredSourceField(StrEnum):
    LEARNING_GOALS = "course_profile.learning_goals"
    ASSESSMENT_STYLES = "course_profile.assessment_styles"
    EXAM_DATE = "course_profile.exam_date"


@dataclass(frozen=True, slots=True)
class TutorConfiguredHint:
    kind: StudyStatementKind
    values: tuple[StudyStatementValue, ...]
    source_field: TutorConfiguredSourceField

    def __post_init__(self) -> None:
        values = tuple(
            StudyStatementInput(self.kind, value).value for value in self.values
        )
        object.__setattr__(self, "values", values)
        if not isinstance(self.source_field, TutorConfiguredSourceField):
            raise TypeError("source_field must be a TutorConfiguredSourceField")
        if not self.values:
            raise ValueError("configured hint values cannot be empty")
        expected = {
            StudyStatementKind.OBJECTIVE: TutorConfiguredSourceField.LEARNING_GOALS,
            StudyStatementKind.ASSESSMENT_FORMAT: TutorConfiguredSourceField.ASSESSMENT_STYLES,
            StudyStatementKind.DEADLINE: TutorConfiguredSourceField.EXAM_DATE,
        }.get(self.kind)
        if expected is None or self.source_field is not expected:
            raise ValueError("configured hint kind and source field disagree")
        if len({_value_key(value) for value in self.values}) != len(self.values):
            raise ValueError("configured hint values must be unique")


@dataclass(frozen=True, slots=True)
class TutorStatementEvidence:
    statement_id: StatementId
    session_id: SessionId
    origin_interaction_id: InteractionId
    value: StudyStatementValue
    recorded_at: datetime

    def __post_init__(self) -> None:
        require_aware(self.recorded_at, "recorded_at")


@dataclass(frozen=True, slots=True)
class TutorContextField:
    kind: StudyStatementKind
    state: TutorContextState
    active: tuple[TutorStatementEvidence, ...] = ()

    def __post_init__(self) -> None:
        object.__setattr__(self, "active", tuple(self.active))
        if not isinstance(self.kind, StudyStatementKind):
            raise TypeError("kind must be a StudyStatementKind")
        if not isinstance(self.state, TutorContextState):
            raise TypeError("state must be a TutorContextState")
        statement_ids = tuple(item.statement_id for item in self.active)
        if len(set(statement_ids)) != len(statement_ids):
            raise ValueError("learner context statement evidence must be unique")
        for evidence in self.active:
            if StudyStatementInput(self.kind, evidence.value).value != evidence.value:
                raise ValueError("learner context evidence value is not canonical")
        if self.state is TutorContextState.MISSING and self.active:
            raise ValueError("missing learner context cannot contain active evidence")
        if self.state is not TutorContextState.MISSING and not self.active:
            raise ValueError("known or conflicting learner context requires evidence")
        if self.state is TutorContextState.CONFLICTING and not self.kind.is_scalar:
            raise ValueError("only scalar learner context can be conflicting")
        distinct = {_value_key(item.value) for item in self.active}
        if self.state is TutorContextState.CONFLICTING and len(distinct) < 2:
            raise ValueError("conflicting learner context requires distinct active values")
        if self.kind.is_scalar and self.state is TutorContextState.KNOWN and len(distinct) > 1:
            raise ValueError("distinct scalar values must be marked conflicting")


@dataclass(frozen=True, slots=True)
class TutorHintDivergence:
    kind: StudyStatementKind
    configured_values: tuple[StudyStatementValue, ...]
    learner_values: tuple[StudyStatementValue, ...]
    learner_statement_ids: tuple[StatementId, ...]

    def __post_init__(self) -> None:
        configured = tuple(
            StudyStatementInput(self.kind, value).value
            for value in self.configured_values
        )
        learner = tuple(
            StudyStatementInput(self.kind, value).value for value in self.learner_values
        )
        object.__setattr__(self, "configured_values", configured)
        object.__setattr__(self, "learner_values", learner)
        object.__setattr__(
            self, "learner_statement_ids", tuple(self.learner_statement_ids)
        )
        if not self.configured_values or not self.learner_values:
            raise ValueError("divergence requires configured and learner values")
        if len({_value_key(value) for value in configured}) != len(configured) or len(
            {_value_key(value) for value in learner}
        ) != len(learner):
            raise ValueError("divergence values must be unique")
        if {_value_key(value) for value in configured} == {
            _value_key(value) for value in learner
        }:
            raise ValueError("equal configured and learner values do not diverge")
        if not self.learner_statement_ids:
            raise ValueError("learner divergence requires statement evidence")
        if len(set(self.learner_statement_ids)) != len(self.learner_statement_ids):
            raise ValueError("learner divergence statement evidence must be unique")


@dataclass(frozen=True, slots=True)
class TutorTimelineEntry:
    kind: TutorTimelineKind
    interaction_id: InteractionId
    occurred_at: datetime
    content: str
    event_id: EventId
    course_sequence: int
    run_id: RunId | None = None
    status: TutorTimelineStatus | None = None
    in_reply_to_interaction_id: InteractionId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, TutorTimelineKind):
            raise TypeError("kind must be a TutorTimelineKind")
        require_aware(self.occurred_at, "occurred_at")
        require_text(self.content, "content")
        if type(self.course_sequence) is not int or self.course_sequence < 1:
            raise ValueError("timeline course_sequence must be positive")
        assistant = self.kind is TutorTimelineKind.ASSISTANT
        if assistant and (self.run_id is None or self.status is None):
            raise ValueError("assistant timeline entries require run and status")
        if not assistant and (
            self.run_id is not None
            or self.status is not None
            or self.in_reply_to_interaction_id is not None
        ):
            raise ValueError("learner and note timeline entries cannot carry assistant linkage")
        if self.status is not None and not isinstance(self.status, TutorTimelineStatus):
            raise TypeError("status must be a TutorTimelineStatus")


@dataclass(frozen=True, slots=True)
class TutorNote:
    interaction_id: InteractionId
    content: str
    occurred_at: datetime
    event_id: EventId
    course_sequence: int

    def __post_init__(self) -> None:
        require_text(self.content, "content")
        require_aware(self.occurred_at, "occurred_at")
        if type(self.course_sequence) is not int or self.course_sequence < 1:
            raise ValueError("note course_sequence must be positive")


@dataclass(frozen=True, slots=True)
class TutorMaterialSummary:
    source_id: SourceId
    current_revision_id: RevisionId
    title: str
    kind: SourceKind
    checksum_sha256: str
    source_role: str
    trust_level: int
    chunk_count: int

    def __post_init__(self) -> None:
        for value, name in ((self.title, "title"), (self.source_role, "source_role")):
            require_text(value, name)
        if not isinstance(self.kind, SourceKind):
            raise TypeError("kind must be a SourceKind")
        if len(self.checksum_sha256) != 64 or any(
            character not in "0123456789abcdef" for character in self.checksum_sha256
        ):
            raise ValueError("checksum_sha256 must be a lowercase SHA-256")
        if type(self.trust_level) is not int or not 0 <= self.trust_level <= 100:
            raise ValueError("trust_level must be between zero and 100")
        if type(self.chunk_count) is not int or self.chunk_count < 1:
            raise ValueError("current material must contain at least one chunk")


@dataclass(frozen=True, slots=True)
class TutorSnapshotV1:
    course_id: CourseId
    session_id: SessionId
    high_water_sequence: int
    session_status: SessionStatus
    continuation_summary: ContinuationSummaryV1 | None
    configured_hints: tuple[TutorConfiguredHint, ...]
    learner_context: tuple[TutorContextField, ...]
    divergences: tuple[TutorHintDivergence, ...]
    timeline: tuple[TutorTimelineEntry, ...]
    notes: tuple[TutorNote, ...]
    materials: tuple[TutorMaterialSummary, ...]
    schema_version: int = TUTOR_SNAPSHOT_SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name in (
            "configured_hints",
            "learner_context",
            "divergences",
            "timeline",
            "notes",
            "materials",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if type(self.schema_version) is not int or self.schema_version != (
            TUTOR_SNAPSHOT_SCHEMA_VERSION
        ):
            raise ValueError("unsupported tutor snapshot schema version")
        if type(self.high_water_sequence) is not int or self.high_water_sequence < 1:
            raise ValueError("snapshot high-water sequence must be positive")
        if not isinstance(self.session_status, SessionStatus):
            raise TypeError("session_status must be a SessionStatus")
        expected_kinds = tuple(StudyStatementKind)
        if tuple(item.kind for item in self.learner_context) != expected_kinds:
            raise ValueError("learner_context must contain all five kinds in canonical order")
        kind_order = {kind: index for index, kind in enumerate(expected_kinds)}
        configured_kinds = tuple(item.kind for item in self.configured_hints)
        if configured_kinds != tuple(sorted(configured_kinds, key=kind_order.__getitem__)) or len(
            set(configured_kinds)
        ) != len(configured_kinds):
            raise ValueError("configured_hints must have unique canonical kind order")
        divergence_kinds = tuple(item.kind for item in self.divergences)
        if divergence_kinds != tuple(sorted(divergence_kinds, key=kind_order.__getitem__)) or len(
            set(divergence_kinds)
        ) != len(divergence_kinds):
            raise ValueError("divergences must have unique canonical kind order")
        configured_by_kind = {item.kind: item for item in self.configured_hints}
        context_by_kind = {item.kind: item for item in self.learner_context}
        expected_divergences = tuple(
            hint.kind
            for hint in self.configured_hints
            if context_by_kind[hint.kind].active
            and {_value_key(value) for value in hint.values}
            != {
                _value_key(item.value)
                for item in context_by_kind[hint.kind].active
            }
        )
        if divergence_kinds != expected_divergences:
            raise ValueError(
                "divergences must exactly report configured and learner disagreement"
            )
        for divergence in self.divergences:
            configured = configured_by_kind.get(divergence.kind)
            context = context_by_kind[divergence.kind]
            if configured is None or configured.values != divergence.configured_values:
                raise ValueError("divergence must reference the configured hint values")
            active_ids = tuple(item.statement_id for item in context.active)
            active_values = {
                _value_key(item.value) for item in context.active
            }
            if divergence.learner_statement_ids != active_ids or {
                _value_key(value) for value in divergence.learner_values
            } != active_values:
                raise ValueError("divergence must reference all active learner evidence")
        sequences = tuple(item.course_sequence for item in self.timeline)
        if sequences != tuple(sorted(sequences)) or len(set(sequences)) != len(sequences):
            raise ValueError("timeline must have unique course-sequence order")
        if any(sequence > self.high_water_sequence for sequence in sequences):
            raise ValueError("timeline cannot exceed the captured high-water sequence")
        event_ids = tuple(item.event_id for item in self.timeline)
        interaction_ids = tuple(item.interaction_id for item in self.timeline)
        if len(set(event_ids)) != len(event_ids) or len(set(interaction_ids)) != len(
            interaction_ids
        ):
            raise ValueError("timeline event and interaction evidence must be unique")
        learner_positions = {
            item.interaction_id: item.course_sequence
            for item in self.timeline
            if item.kind is TutorTimelineKind.LEARNER
        }
        for item in self.timeline:
            if item.in_reply_to_interaction_id is not None and learner_positions.get(
                item.in_reply_to_interaction_id, self.high_water_sequence + 1
            ) >= item.course_sequence:
                raise ValueError("assistant reply must target an earlier learner turn")
        note_evidence = tuple(
            (item.event_id, item.course_sequence, item.interaction_id, item.content)
            for item in self.timeline
            if item.kind is TutorTimelineKind.NOTE
        )
        if tuple(
            (item.event_id, item.course_sequence, item.interaction_id, item.content)
            for item in self.notes
        ) != note_evidence:
            raise ValueError("notes must exactly mirror note timeline evidence")
        material_ids = tuple(item.source_id for item in self.materials)
        if material_ids != tuple(sorted(material_ids, key=str)) or len(
            set(material_ids)
        ) != len(material_ids):
            raise ValueError("materials must have unique source-id order")

    def to_json(self) -> JsonObject:
        return {
            "schema_version": self.schema_version,
            "course_id": str(self.course_id),
            "session_id": str(self.session_id),
            "high_water_sequence": self.high_water_sequence,
            "session": {
                "status": self.session_status.value,
                "continuation_summary": _summary_json(self.continuation_summary),
            },
            "configured_hints": tuple(
                {
                    "kind": item.kind.value,
                    "values": tuple(_value_json(value) for value in item.values),
                    "source_field": item.source_field.value,
                }
                for item in self.configured_hints
            ),
            "learner_context": tuple(
                {
                    "kind": item.kind.value,
                    "state": item.state.value,
                    "active": tuple(
                        {
                            "statement_id": str(evidence.statement_id),
                            "session_id": str(evidence.session_id),
                            "origin_interaction_id": str(
                                evidence.origin_interaction_id
                            ),
                            "value": _value_json(evidence.value),
                            "recorded_at": _timestamp(evidence.recorded_at),
                        }
                        for evidence in item.active
                    ),
                }
                for item in self.learner_context
            ),
            "divergences": tuple(
                {
                    "kind": item.kind.value,
                    "configured_values": tuple(
                        _value_json(value) for value in item.configured_values
                    ),
                    "learner_values": tuple(
                        _value_json(value) for value in item.learner_values
                    ),
                    "learner_statement_ids": tuple(
                        str(statement_id) for statement_id in item.learner_statement_ids
                    ),
                }
                for item in self.divergences
            ),
            "timeline": tuple(_timeline_json(item) for item in self.timeline),
            "notes": tuple(
                {
                    "interaction_id": str(item.interaction_id),
                    "content": item.content,
                    "occurred_at": _timestamp(item.occurred_at),
                    "event_id": str(item.event_id),
                    "course_sequence": item.course_sequence,
                }
                for item in self.notes
            ),
            "materials": tuple(
                {
                    "source_id": str(item.source_id),
                    "current_revision_id": str(item.current_revision_id),
                    "title": item.title,
                    "kind": item.kind.value,
                    "checksum_sha256": item.checksum_sha256,
                    "source_role": item.source_role,
                    "trust_level": item.trust_level,
                    "chunk_count": item.chunk_count,
                }
                for item in self.materials
            ),
        }


def _value_key(value: StudyStatementValue) -> tuple[str, str]:
    encoded = value.isoformat() if isinstance(value, date) else str(value)
    return type(value).__name__, encoded

def _value_json(value: StudyStatementValue) -> JsonValue:
    return value.isoformat() if isinstance(value, date) else value


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _summary_json(summary: ContinuationSummaryV1 | None) -> JsonValue:
    if summary is None:
        return None
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


def _timeline_json(item: TutorTimelineEntry) -> JsonObject:
    return {
        "kind": item.kind.value,
        "interaction_id": str(item.interaction_id),
        "occurred_at": _timestamp(item.occurred_at),
        "content": item.content,
        "event_id": str(item.event_id),
        "course_sequence": item.course_sequence,
        "run_id": None if item.run_id is None else str(item.run_id),
        "status": None if item.status is None else item.status.value,
        "in_reply_to_interaction_id": (
            None
            if item.in_reply_to_interaction_id is None
            else str(item.in_reply_to_interaction_id)
        ),
    }


__all__ = [
    "TUTOR_SNAPSHOT_SCHEMA_VERSION",
    "TutorConfiguredHint",
    "TutorConfiguredSourceField",
    "TutorContextField",
    "TutorContextState",
    "TutorHintDivergence",
    "TutorMaterialSummary",
    "TutorNote",
    "TutorSnapshotV1",
    "TutorStatementEvidence",
    "TutorTimelineEntry",
    "TutorTimelineKind",
    "TutorTimelineStatus",
]
