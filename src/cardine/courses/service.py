"""Application service for immutable canonical course creation."""

from __future__ import annotations

from study_agent.domain.context import ExecutionContext
from study_agent.domain.course import CourseProfile
from study_agent.domain.events import Actor, DomainEvent, PrincipalKind
from study_agent.domain.identifiers import CourseId
from study_agent.ports import ClockPort, CourseNotFoundError, CourseViewPort, EventStore
from study_agent.ports.storage import EventSequenceConflictError

from .events import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    course_event_id_for,
    course_profile_manifest,
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

    def _existing(self, course_id: CourseId) -> CourseProfile | None:
        try:
            return self._view.get(course_id)
        except CourseNotFoundError:
            return None


def _same_or_conflict(existing: CourseProfile, requested: CourseProfile) -> CourseProfile:
    if existing == requested:
        return existing
    raise CourseConflictError("course id already belongs to a different immutable profile")
