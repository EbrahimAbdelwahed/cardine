"""Immutable domain values for progressively collected learner context."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum

from ._validation import require_aware, require_text
from .identifiers import CourseId, EventId, InteractionId, SessionId, StatementId

type StudyStatementValue = str | date | int


class StudyStatementKind(StrEnum):
    OBJECTIVE = "objective"
    DEADLINE = "deadline"
    WEEKLY_TIME_BUDGET = "weekly_time_budget"
    ASSESSMENT_FORMAT = "assessment_format"
    TESTING_PREFERENCE = "testing_preference"

    @property
    def is_scalar(self) -> bool:
        return self in (self.DEADLINE, self.WEEKLY_TIME_BUDGET)


class StatementStatus(StrEnum):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


@dataclass(frozen=True, slots=True)
class StudyStatementInput:
    kind: StudyStatementKind
    value: StudyStatementValue

    def __post_init__(self) -> None:
        if not isinstance(self.kind, StudyStatementKind):
            raise TypeError("kind must be a StudyStatementKind")
        canonical = canonical_statement_value(self.kind, self.value)
        object.__setattr__(self, "value", canonical)


@dataclass(frozen=True, slots=True)
class StudyContextStatement:
    id: StatementId
    course_id: CourseId
    session_id: SessionId
    origin_interaction_id: InteractionId
    kind: StudyStatementKind
    value: StudyStatementValue
    status: StatementStatus
    recorded_at: datetime

    def __post_init__(self) -> None:
        canonical = canonical_statement_value(self.kind, self.value)
        object.__setattr__(self, "value", canonical)
        require_aware(self.recorded_at, "recorded_at")


@dataclass(frozen=True, slots=True)
class StudyContextResolution:
    event_id: EventId
    kind: StudyStatementKind
    selected_statement_id: StatementId
    superseded_statement_ids: tuple[StatementId, ...]
    resolved_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "superseded_statement_ids", tuple(self.superseded_statement_ids)
        )
        if not self.kind.is_scalar:
            raise ValueError("only scalar statement kinds can be resolved")
        if self.selected_statement_id in self.superseded_statement_ids:
            raise ValueError("selected statement cannot also be superseded")
        if len(set(self.superseded_statement_ids)) != len(self.superseded_statement_ids):
            raise ValueError("superseded statement ids must be unique")
        require_aware(self.resolved_at, "resolved_at")


@dataclass(frozen=True, slots=True)
class StudyContextConflict:
    kind: StudyStatementKind
    statement_ids: tuple[StatementId, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "statement_ids", tuple(self.statement_ids))
        if not self.kind.is_scalar:
            raise ValueError("only scalar statement kinds can conflict")
        if len(self.statement_ids) < 2 or len(set(self.statement_ids)) != len(
            self.statement_ids
        ):
            raise ValueError("a conflict requires at least two unique statements")


@dataclass(frozen=True, slots=True)
class StudyContextSnapshot:
    course_id: CourseId
    sequence: int
    statements: tuple[StudyContextStatement, ...] = ()
    resolutions: tuple[StudyContextResolution, ...] = ()
    conflicts: tuple[StudyContextConflict, ...] = ()

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("study context sequence must be positive")
        object.__setattr__(self, "statements", tuple(self.statements))
        object.__setattr__(self, "resolutions", tuple(self.resolutions))
        object.__setattr__(self, "conflicts", tuple(self.conflicts))

    def statement(self, statement_id: StatementId) -> StudyContextStatement:
        for statement in self.statements:
            if statement.id == statement_id:
                return statement
        raise LookupError(f"study-context statement {statement_id} was not found")

    def active(self, kind: StudyStatementKind) -> tuple[StudyContextStatement, ...]:
        return tuple(
            item
            for item in self.statements
            if item.kind is kind and item.status is StatementStatus.ACTIVE
        )


def canonical_statement_value(
    kind: StudyStatementKind, value: StudyStatementValue
) -> StudyStatementValue:
    if kind in (
        StudyStatementKind.OBJECTIVE,
        StudyStatementKind.ASSESSMENT_FORMAT,
        StudyStatementKind.TESTING_PREFERENCE,
    ):
        if not isinstance(value, str):
            raise TypeError(f"{kind.value} must be text")
        canonical = value.strip()
        require_text(canonical, kind.value)
        return canonical
    if kind is StudyStatementKind.DEADLINE:
        if not isinstance(value, date) or isinstance(value, datetime):
            raise TypeError("deadline must be a calendar date")
        return value
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("weekly_time_budget must be an integer")
    if not 1 <= value <= 10_080:
        raise ValueError("weekly_time_budget must be between 1 and 10080 minutes")
    return value
