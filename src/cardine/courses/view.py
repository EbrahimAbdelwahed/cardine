"""Projection-backed canonical course reader."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from study_agent.domain.course import CourseProfile
from study_agent.domain.identifiers import CourseId
from study_agent.ports.course import CourseNotFoundError
from study_agent.state import Projection

from .events import decode_course_profile

type ProjectionLoader = Callable[[CourseId], Projection]
type CourseIdLoader = Callable[[], Sequence[CourseId]]


class ProjectionCourseView:
    def __init__(self, load_projection: ProjectionLoader) -> None:
        self._load_projection = load_projection

    def get(self, course_id: CourseId) -> CourseProfile:
        projection = self._load_projection(course_id)
        if projection.course_id != course_id:
            raise ValueError("projection loader returned another course")
        if "course" not in projection.state:
            raise CourseNotFoundError(course_id)
        raw = projection.state["course"]
        if not isinstance(raw, Mapping):
            raise ValueError("course projection state is corrupt")
        profile = decode_course_profile(raw)
        if profile.id != course_id:
            raise ValueError("course projection ownership is corrupt")
        return profile


class ProjectionCourseCatalog:
    """Enumerate canonical course projections through an explicit read seam."""

    def __init__(self, list_course_ids: CourseIdLoader, view: ProjectionCourseView) -> None:
        self._list_course_ids = list_course_ids
        self._view = view

    def list_courses(self) -> tuple[CourseProfile, ...]:
        course_ids = tuple(self._list_course_ids())
        if any(type(course_id) is not CourseId for course_id in course_ids):
            raise ValueError("course catalog returned an invalid course id")
        if len(set(course_ids)) != len(course_ids):
            raise ValueError("course catalog returned duplicate course ids")
        return tuple(self._view.get(course_id) for course_id in sorted(course_ids, key=str))
