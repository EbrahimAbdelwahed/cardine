from __future__ import annotations

from datetime import UTC, datetime

from cardine.courses import CourseService, ProjectionCourseView
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
)


class CourseClock:
    def now(self) -> datetime:
        return datetime(2026, 7, 10, 8, tzinfo=UTC)


def canonical_profile(course_id: CourseId) -> CourseProfile:
    return CourseProfile(
        course_id,
        "Fixture course",
        "en",
        learning_goals=("Verify study behavior",),
    )


class ExistingCourseView:
    """Narrow test double for isolated failure tests with non-projecting stores."""

    def get(self, course_id: CourseId) -> CourseProfile:
        return canonical_profile(course_id)


def create_canonical_course(
    events: SQLiteEventStore, course_id: CourseId
) -> ProjectionCourseView:
    view = ProjectionCourseView(events.projection)
    CourseService(events, CourseClock(), view).create(
        canonical_profile(course_id),
        ExecutionContext(
            PrincipalKind.SERVICE,
            "fixture-course-service",
            course_id,
            CorrelationId("fixture-course-created"),
        ),
    )
    return view
