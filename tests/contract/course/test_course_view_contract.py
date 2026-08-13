from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from cardine.courses import (
    CourseService,
    ProjectionCourseCatalog,
    ProjectionCourseView,
    register_course_events,
)
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
)
from study_agent.ports import CourseCatalogPort, CourseNotFoundError, CourseViewPort
from study_agent.state import EventRegistry, Projection


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 7, 12, 8, tzinfo=UTC)


def test_projection_view_satisfies_port_and_missing_is_typed(tmp_path: Path) -> None:
    registry = EventRegistry()
    register_course_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    view = ProjectionCourseView(events.projection)
    typed: CourseViewPort = view
    course_id = CourseId("course-1")

    with pytest.raises(CourseNotFoundError):
        typed.get(course_id)

    expected = CourseProfile(course_id, "Course", "en", learning_goals=("Learn",))
    context = ExecutionContext(
        PrincipalKind.SERVICE,
        "course-service",
        course_id,
        CorrelationId("correlation-1"),
    )
    CourseService(events, Clock(), view).create(expected, context)

    assert typed.get(course_id) == expected


@pytest.mark.parametrize("corrupt", [None, "not-an-object", ()])
def test_present_invalid_course_state_is_corruption_not_absence(corrupt: object) -> None:
    course_id = CourseId("course-1")
    view = ProjectionCourseView(
        lambda requested: Projection(requested, 1, {"course": corrupt})  # type: ignore[dict-item]
    )

    with pytest.raises(ValueError, match="corrupt"):
        view.get(course_id)


def test_projection_catalog_is_empty_safe_and_canonical_event_derived(tmp_path: Path) -> None:
    registry = EventRegistry()
    register_course_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    view = ProjectionCourseView(events.projection)
    catalog: CourseCatalogPort = ProjectionCourseCatalog(events.list_course_ids, view)
    assert catalog.list_courses() == ()

    for course_id, title in ((CourseId("z-course"), "Z"), (CourseId("a-course"), "A")):
        CourseService(events, Clock(), view).create(
            CourseProfile(course_id, title, "en", learning_goals=("Learn",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "course-service",
                course_id,
                CorrelationId(f"correlation-{course_id}"),
            ),
        )

    assert tuple(profile.id for profile in catalog.list_courses()) == (
        CourseId("a-course"),
        CourseId("z-course"),
    )


def test_projection_catalog_rejects_duplicate_or_invalid_ids() -> None:
    course_id = CourseId("course")
    view = ProjectionCourseView(
        lambda requested: Projection(
            requested,
            state={
                "course": {
                    "id": str(requested),
                    "title": "Course",
                    "language": "en",
                    "exam_date": None,
                    "assessment_styles": (),
                    "learning_goals": (),
                    "source_policy": "course_material_only",
                    "terminology_policy": "preserve_source_terms",
                }
            },
        )
    )
    with pytest.raises(ValueError, match="duplicate"):
        ProjectionCourseCatalog(lambda: (course_id, course_id), view).list_courses()
    with pytest.raises(ValueError, match="invalid"):
        ProjectionCourseCatalog(lambda: ("course",), view).list_courses()  # type: ignore[arg-type,return-value]
