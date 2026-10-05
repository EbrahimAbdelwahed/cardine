"""Application service for immutable canonical course creation."""

from __future__ import annotations

from cardine.domain.course import CourseProfile
from study_agent.domain.context import ExecutionContext
from study_agent.domain.events import Actor, DomainEvent, PrincipalKind
from study_agent.domain.identifiers import CourseId
from study_agent.ports import ClockPort, CourseNotFoundError, CourseViewPort, EventStore
from study_agent.ports.storage import EventSequenceConflictError

from .events import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    COURSE_STUDY_PLAN_SET,
    STUDY_PLAN_SCHEMA_VERSION,
    StudyPlan,
    course_event_id_for,
    course_profile_manifest,
    decode_study_plan,
    study_plan_event_id,
    study_plan_manifest,
)


class CourseCommandError(ValueError):
    """A course command violates ownership or trusted-actor rules."""


class CourseConflictError(CourseCommandError):
    """A course id already names different immutable profile data."""


class RetryableCourseConflictError(RuntimeError):
    """The stream raced without committing the requested course profile."""


class CourseService:
    def __init__(
        self, events: EventStore, clock: ClockPort, view: CourseViewPort
    ) -> None:
        self._events = events
        self._clock = clock
        self._view = view

    def create(
        self,
        profile: CourseProfile,
        context: ExecutionContext,
        *,
        expected_sequence: int | None = None,
    ) -> CourseProfile:
        if context.course_id != profile.id:
            raise CourseCommandError("execution context course must match profile id")
        if context.session_id is not None:
            raise CourseCommandError("course creation cannot be session-scoped")
        if not isinstance(context.principal_kind, PrincipalKind) or context.principal_kind not in (
            PrincipalKind.HUMAN,
            PrincipalKind.SERVICE,
        ):
            raise CourseCommandError("course creation requires a trusted human or service actor")
        stream = tuple(self._events.read(profile.id))
        sequence = stream[-1].course_sequence if stream else 0
        if expected_sequence is not None and sequence != expected_sequence:
            raise RetryableCourseConflictError(
                "course stream does not match expected sequence "
                f"{expected_sequence}; observed {sequence}"
            )
        existing = self._existing(profile.id)
        if existing is not None:
            if expected_sequence is not None:
                latest = tuple(self._events.read(profile.id))
                latest_sequence = latest[-1].course_sequence if latest else 0
                if latest_sequence != expected_sequence:
                    raise RetryableCourseConflictError(
                        "course stream advanced before idempotent return; "
                        f"expected {expected_sequence}, observed {latest_sequence}"
                    )
            return _same_or_conflict(existing, profile)
        event = DomainEvent(
            course_event_id_for(profile),
            profile.id,
            sequence + 1,
            COURSE_CREATED,
            COURSE_SCHEMA_VERSION,
            Actor(context.principal_kind, context.principal_id),
            self._clock.now(),
            context.correlation_id,
            course_profile_manifest(profile),
        )
        try:
            self._events.append(profile.id, sequence, (event,))
        except EventSequenceConflictError as error:
            if expected_sequence is not None:
                raise RetryableCourseConflictError(
                    "course stream advanced before creation committed"
                ) from error
            raced = self._existing(profile.id)
            if raced is not None:
                return _same_or_conflict(raced, profile)
            raise RetryableCourseConflictError(
                "course stream advanced before creation committed"
            ) from error
        return self._view.get(profile.id)

    def set_study_plan(self, plan: StudyPlan, context: ExecutionContext) -> StudyPlan:
        """Record the learner's exam date and rhythm as one explicit decision.

        The idempotency key names the decision: an exact retry returns the
        committed plan, changed content under the same key is a conflict.
        """
        if context.session_id is not None:
            raise CourseCommandError("a study plan is course-scoped")
        if context.principal_kind not in (PrincipalKind.HUMAN, PrincipalKind.SERVICE):
            raise CourseCommandError("a study plan requires a trusted human or service actor")
        if not context.idempotency_key:
            raise CourseCommandError("a study plan requires an idempotency key")
        course_id = context.course_id
        self._view.get(course_id)
        event_id = study_plan_event_id(course_id, context.idempotency_key)
        manifest = study_plan_manifest(plan)
        for _attempt in range(3):
            stream = tuple(self._events.read(course_id))
            for previous in stream:
                if previous.event_id == event_id:
                    committed = decode_study_plan(previous.payload)
                    if committed != plan:
                        raise CourseConflictError("study plan request changed content")
                    return committed
            sequence = stream[-1].course_sequence if stream else 0
            event = DomainEvent(
                event_id,
                course_id,
                sequence + 1,
                COURSE_STUDY_PLAN_SET,
                STUDY_PLAN_SCHEMA_VERSION,
                Actor(context.principal_kind, context.principal_id),
                self._clock.now(),
                context.correlation_id,
                manifest,
            )
            try:
                self._events.append(course_id, sequence, (event,))
            except EventSequenceConflictError:
                continue
            return plan
        raise RetryableCourseConflictError("course stream kept advancing; retry the plan")

    def _existing(self, course_id: CourseId) -> CourseProfile | None:
        try:
            return self._view.get(course_id)
        except CourseNotFoundError:
            return None


def _same_or_conflict(existing: CourseProfile, requested: CourseProfile) -> CourseProfile:
    if existing == requested:
        return existing
    raise CourseConflictError("course id already belongs to a different immutable profile")
