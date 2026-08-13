"""Immutable event-sourced course profiles."""

from .events import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    CourseCreated,
    course_command_fingerprint,
    course_event_id_for,
    course_profile_manifest,
    decode_course_created,
    decode_course_profile,
)
from .projection import reduce_course_created, register_course_events
from .service import (
    CourseCommandError,
    CourseConflictError,
    CourseService,
    RetryableCourseConflictError,
)
from .view import ProjectionCourseCatalog, ProjectionCourseView

__all__ = [
    "COURSE_CREATED",
    "COURSE_SCHEMA_VERSION",
    "CourseCommandError",
    "CourseConflictError",
    "CourseCreated",
    "CourseService",
    "ProjectionCourseCatalog",
    "ProjectionCourseView",
    "RetryableCourseConflictError",
    "course_command_fingerprint",
    "course_event_id_for",
    "course_profile_manifest",
    "decode_course_created",
    "decode_course_profile",
    "reduce_course_created",
    "register_course_events",
]
