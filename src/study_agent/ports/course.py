"""Projection-only public course query contract."""

from __future__ import annotations

from typing import Protocol

from cardine.domain.course import CourseProfile
from study_agent.domain.identifiers import CourseId


class CourseNotFoundError(LookupError):
    def __init__(self, course_id: CourseId) -> None:
        self.course_id = course_id
        super().__init__(f"course {course_id} was not found")


class CourseViewPort(Protocol):
    def get(self, course_id: CourseId) -> CourseProfile: ...


class CourseCatalogPort(Protocol):
    """Read-only deterministic enumeration of canonical course projections."""

    def list_courses(self) -> tuple[CourseProfile, ...]: ...
