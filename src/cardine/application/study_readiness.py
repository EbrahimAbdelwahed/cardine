"""Projection-only, attributable study-readiness facts for Cardine."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime
from types import MappingProxyType
from typing import cast

from study_agent.artifacts import ExamBlueprintContent, ProjectionArtifactView
from study_agent.artifacts.content import EvidenceObservation
from study_agent.artifacts.contracts import ArtifactSnapshot
from study_agent.assessments import (
    ProjectionAssessmentView,
    ProjectionLearnerEvidenceView,
)
from study_agent.assessments.evidence import LearnerEvidenceSnapshot
from cardine.courses import ProjectionCourseView
from study_agent.domain import (
    CourseId,
    StatementStatus,
    StudyArtifactKind,
    StudyContextSnapshot,
    StudyStatementKind,
)
from study_agent.ports.clock import ClockPort
from study_agent.recall import DueRecallView
from study_agent.state import Projection
from study_agent.study_context import ProjectionStudyContextView

type ProjectionLoader = Callable[[CourseId], Projection]


@dataclass(frozen=True, slots=True)
class ReadinessSource:
    projection: str
    sequence: int

    def to_json(self) -> dict[str, object]:
        return {"projection": self.projection, "sequence": self.sequence}


@dataclass(frozen=True, slots=True)
class AttributedValue:
    value: str
    source: ReadinessSource

    def __str__(self) -> str:
        return self.value

    def to_json(self) -> dict[str, object]:
        return {"value": self.value, "source": self.source.to_json()}


@dataclass(frozen=True, slots=True)
class ReadinessConstraint:
    kind: str
    value: str | int
    origin: str
    status: str
    source: ReadinessSource
    statement_id: str | None = None

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "value": self.value,
            "origin": self.origin,
            "status": self.status,
            "statement_id": self.statement_id,
            "source": self.source.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ReadinessBlueprint:
    revision_id: str
    sample_size: int
    observed_topics: tuple[Mapping[str, object], ...]
    observed_formats: tuple[Mapping[str, object], ...]
    limitations: tuple[str, ...]
    source: ReadinessSource

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "observed_topics",
            tuple(MappingProxyType(dict(item)) for item in self.observed_topics),
        )
        object.__setattr__(
            self,
            "observed_formats",
            tuple(MappingProxyType(dict(item)) for item in self.observed_formats),
        )
        object.__setattr__(self, "limitations", tuple(self.limitations))

    def to_json(self) -> dict[str, object]:
        return {
            "revision_id": self.revision_id,
            "sample_size": self.sample_size,
            "observed_topics": tuple(dict(item) for item in self.observed_topics),
            "observed_formats": tuple(dict(item) for item in self.observed_formats),
            "limitations": self.limitations,
            "source": self.source.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ReadinessArtifactCount:
    kind: str
    pending: int
    accepted: int
    source: ReadinessSource

    def to_json(self) -> dict[str, object]:
        return {
            "kind": self.kind,
            "pending": self.pending,
            "accepted": self.accepted,
            "source": self.source.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ReadinessEvidenceReference:
    grade_id: str
    event_sequence: int
    disposition: str
    numerator: int
    denominator: int

    def to_json(self) -> dict[str, object]:
        return {
            "grade_id": self.grade_id,
            "event_sequence": self.event_sequence,
            "disposition": self.disposition,
            "numerator": self.numerator,
            "denominator": self.denominator,
        }


@dataclass(frozen=True, slots=True)
class ReadinessEvidence:
    dimension: str
    key: str
    label: str
    numerator: int
    denominator: int
    through_sequence: int
    references: tuple[ReadinessEvidenceReference, ...]
    source: ReadinessSource

    def to_json(self) -> dict[str, object]:
        return {
            "dimension": self.dimension,
            "key": self.key,
            "label": self.label,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "through_sequence": self.through_sequence,
            "references": tuple(item.to_json() for item in self.references),
            "source": self.source.to_json(),
        }


@dataclass(frozen=True, slots=True)
class ReadinessRecall:
    available: bool
    due_count: int | None
    earliest_due_at: datetime | None
    source: ReadinessSource

    def to_json(self) -> dict[str, object]:
        return {
            "available": self.available,
            "due_count": self.due_count,
            "earliest_due_at": (
                None
                if self.earliest_due_at is None
                else self.earliest_due_at.isoformat().replace("+00:00", "Z")
            ),
            "source": self.source.to_json(),
        }


@dataclass(frozen=True, slots=True)
class StudyReadinessSnapshot:
    course_id: CourseId
    sequence: int
    as_of_date: date
    exam_date: date | None
    days_remaining: int | None
    deadline_status: str
    learning_goals: tuple[AttributedValue, ...]
    assessment_styles: tuple[AttributedValue, ...]
    constraints: tuple[ReadinessConstraint, ...]
    blueprints: tuple[ReadinessBlueprint, ...]
    artifact_counts: tuple[ReadinessArtifactCount, ...]
    evidence: tuple[ReadinessEvidence, ...]
    recall: ReadinessRecall
    sources: Mapping[str, ReadinessSource]

    def __post_init__(self) -> None:
        if not isinstance(self.course_id, CourseId):
            raise TypeError("readiness course_id must be CourseId")
        for name in (
            "learning_goals",
            "assessment_styles",
            "constraints",
            "blueprints",
            "artifact_counts",
            "evidence",
        ):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        object.__setattr__(self, "sources", MappingProxyType(dict(self.sources)))

    @property
    def source(self) -> ReadinessSource:
        return self.sources["projection"]

    def to_json(self) -> dict[str, object]:
        clock_source = self.sources["injected_clock"].to_json()
        course_source = self.sources["course"].to_json()
        return {
            "schema_version": 1,
            "course_id": str(self.course_id),
            "high_water_sequence": self.sequence,
            "as_of_date": self.as_of_date.isoformat(),
            "exam_date": None if self.exam_date is None else self.exam_date.isoformat(),
            "days_remaining": self.days_remaining,
            "deadline_status": self.deadline_status,
            "exam": {
                "date": None if self.exam_date is None else self.exam_date.isoformat(),
                "as_of_date": self.as_of_date.isoformat(),
                "days_remaining": self.days_remaining,
                "deadline_status": self.deadline_status,
                "sources": {
                    "configured_date": course_source,
                    "as_of_date": clock_source,
                    "deadline_status": {
                        "configured_date": course_source,
                        "conflict_state": self.sources["study_context"].to_json(),
                    },
                    "days_remaining": {
                        "configured_date": course_source,
                        "conflict_state": self.sources["study_context"].to_json(),
                        "as_of_date": clock_source,
                    },
                },
            },
            "learning_goals": tuple(item.to_json() for item in self.learning_goals),
            "assessment_styles": tuple(
                item.to_json() for item in self.assessment_styles
            ),
            "constraints": tuple(item.to_json() for item in self.constraints),
            "blueprints": tuple(item.to_json() for item in self.blueprints),
            "artifact_counts": tuple(item.to_json() for item in self.artifact_counts),
            "evidence": tuple(item.to_json() for item in self.evidence),
            "recall": self.recall.to_json(),
            "source": self.source.to_json(),
            "sources": {
                key: value.to_json() for key, value in self.sources.items()
            },
        }


class StudyReadinessView:
    """Build attributable facts from exactly one immutable projection capture."""

    def __init__(
        self,
        projection: Projection | ProjectionLoader,
        clock: ClockPort,
        *,
        recall_available: bool | None = None,
    ) -> None:
        if not callable(getattr(clock, "now", None)):
            raise TypeError("readiness clock must implement now()")
        self._projection = projection
        self._clock = clock
        self._recall_available = recall_available

    @classmethod
    def from_projection(
        cls,
        projection: Projection,
        clock: ClockPort,
        *,
        recall_available: bool | None = None,
    ) -> StudyReadinessView:
        return cls(projection, clock, recall_available=recall_available)

    def get(self, course_id: CourseId | None = None) -> StudyReadinessSnapshot:
        projection = (
            self._projection
            if isinstance(self._projection, Projection)
            else self._projection(cast(CourseId, course_id))
        )
        if not isinstance(projection, Projection):
            raise TypeError("projection loader returned invalid projection")
        if course_id is not None and projection.course_id != course_id:
            raise ValueError("projection loader returned another course")
        now = self._clock.now()
        if (
            not isinstance(now, datetime)
            or now.tzinfo is None
            or now.utcoffset() is None
        ):
            raise ValueError("readiness clock must return aware UTC datetime")
        if now.utcoffset() != UTC.utcoffset(now):
            raise ValueError("readiness clock must return UTC datetime")
        now = now.astimezone(UTC)

        def loader(_course_id: CourseId) -> Projection:
            return projection
        course = ProjectionCourseView(loader).get(projection.course_id)
        context = ProjectionStudyContextView(loader).get(projection.course_id)
        artifacts = ProjectionArtifactView(loader).get(projection.course_id)
        assessments = ProjectionAssessmentView(loader).get(projection.course_id)
        learner_evidence = ProjectionLearnerEvidenceView(
            ProjectionAssessmentView(loader)
        ).get(projection.course_id)
        sources = {
            key: ReadinessSource(key, sequence)
            for key, sequence in (
                ("projection", projection.sequence),
                ("course", projection.sequence),
                ("study_context", context.sequence),
                ("study_artifacts", artifacts.sequence),
                ("assessments", assessments.sequence),
                ("learner_evidence", learner_evidence.through_sequence),
                ("recall", projection.sequence),
                ("injected_clock", projection.sequence),
            )
        }
        deadline_conflicted = any(
            item.kind is StudyStatementKind.DEADLINE for item in context.conflicts
        )
        deadline_status = (
            "conflicted"
            if deadline_conflicted
            else "missing"
            if course.exam_date is None
            else "configured"
        )
        days_remaining = None
        if not deadline_conflicted and course.exam_date is not None:
            days_remaining = (course.exam_date - now.date()).days
        return StudyReadinessSnapshot(
            projection.course_id,
            projection.sequence,
            now.date(),
            course.exam_date,
            days_remaining,
            deadline_status,
            tuple(
                AttributedValue(value, sources["course"])
                for value in course.learning_goals
            ),
            tuple(
                AttributedValue(value, sources["course"])
                for value in course.assessment_styles
            ),
            _constraints(context, sources["study_context"]),
            _blueprints(artifacts, sources["study_artifacts"]),
            _counts(artifacts, sources["study_artifacts"]),
            _evidence(learner_evidence, sources["learner_evidence"]),
            self._recall(projection, now, sources["recall"]),
            sources,
        )

    build = get

    def _recall(
        self,
        projection: Projection,
        now: datetime,
        source: ReadinessSource,
    ) -> ReadinessRecall:
        if self._recall_available is False:
            return ReadinessRecall(False, None, None, source)
        if "recall" not in projection.state:
            return (
                ReadinessRecall(True, 0, None, source)
                if self._recall_available is True
                else ReadinessRecall(False, None, None, source)
            )
        try:
            due = DueRecallView(lambda _course_id: projection, self._clock).due(
                projection.course_id, now=now
            )
        except (KeyError, LookupError, ValueError):
            return ReadinessRecall(False, None, None, source)
        return ReadinessRecall(
            True,
            len(due),
            None if not due else due[0].due_at,
            source,
        )


def _constraints(
    snapshot: StudyContextSnapshot, source: ReadinessSource
) -> tuple[ReadinessConstraint, ...]:
    conflicts = {item.kind for item in snapshot.conflicts}
    rows = []
    for statement in snapshot.statements:
        if (
            statement.kind
            not in (
                StudyStatementKind.DEADLINE,
                StudyStatementKind.WEEKLY_TIME_BUDGET,
            )
            or statement.status is not StatementStatus.ACTIVE
        ):
            continue
        value = (
            statement.value.isoformat()
            if isinstance(statement.value, date)
            else statement.value
        )
        rows.append(
            ReadinessConstraint(
                statement.kind.value,
                value,
                "learner",
                "conflicted" if statement.kind in conflicts else "active",
                source,
                str(statement.id),
            )
        )
    return tuple(sorted(rows, key=lambda item: (item.kind, item.statement_id or "")))


def _blueprints(
    snapshot: ArtifactSnapshot, source: ReadinessSource
) -> tuple[ReadinessBlueprint, ...]:
    rows = []
    for revision in snapshot.accepted(StudyArtifactKind.EXAM_BLUEPRINT):
        content = revision.content.content
        if not isinstance(content, ExamBlueprintContent):
            continue
        rows.append(
            ReadinessBlueprint(
                str(revision.id),
                content.sample_size,
                tuple(_observation(item) for item in content.observed_topics),
                tuple(_observation(item) for item in content.observed_formats),
                content.limitations,
                source,
            )
        )
    return tuple(sorted(rows, key=lambda item: item.revision_id))


def _observation(value: EvidenceObservation) -> Mapping[str, object]:
    return MappingProxyType(
        {
            "value": value.value,
            "source_commitment_indices": tuple(value.source_commitment_indices),
        }
    )


def _counts(
    snapshot: ArtifactSnapshot, source: ReadinessSource
) -> tuple[ReadinessArtifactCount, ...]:
    pending = snapshot.pending()
    accepted = snapshot.accepted()
    return tuple(
        ReadinessArtifactCount(
            kind.value,
            sum(item.kind is kind for item in pending),
            sum(item.kind is kind for item in accepted),
            source,
        )
        for kind in StudyArtifactKind
    )


def _evidence(
    snapshot: LearnerEvidenceSnapshot, source: ReadinessSource
) -> tuple[ReadinessEvidence, ...]:
    return tuple(
        ReadinessEvidence(
            item.dimension.value,
            item.key,
            item.key if item.dimension.value == "criterion" else item.label,
            item.numerator,
            item.denominator,
            item.through_sequence,
            tuple(
                ReadinessEvidenceReference(
                    str(reference.grade_id),
                    reference.event_sequence,
                    reference.disposition.value,
                    reference.numerator,
                    reference.denominator,
                )
                for reference in item.evidence
            ),
            source,
        )
        for item in snapshot.estimates
    )


__all__ = [
    "AttributedValue",
    "ReadinessArtifactCount",
    "ReadinessBlueprint",
    "ReadinessConstraint",
    "ReadinessEvidence",
    "ReadinessEvidenceReference",
    "ReadinessRecall",
    "ReadinessSource",
    "StudyReadinessSnapshot",
    "StudyReadinessView",
]
