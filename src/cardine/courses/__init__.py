"""Immutable event-sourced course profiles."""

from .events import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    COURSE_STUDY_PLAN_SET,
    STUDY_PLAN_SCHEMA_VERSION,
    CourseCreated,
    StudyPlan,
    StudyPlanSet,
    course_command_fingerprint,
    course_event_id_for,
    course_profile_manifest,
    decode_course_created,
    decode_course_profile,
    decode_study_plan,
    decode_study_plan_set,
    study_plan_manifest,
)
from .projection import reduce_course_created, reduce_study_plan_set, register_course_events
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
    "COURSE_STUDY_PLAN_SET",
    "STUDY_PLAN_SCHEMA_VERSION",
    "CourseCommandError",
    "CourseConflictError",
    "CourseCreated",
    "CourseService",
    "ProjectionCourseCatalog",
    "ProjectionCourseView",
    "RetryableCourseConflictError",
    "StudyPlan",
    "StudyPlanSet",
    "course_command_fingerprint",
    "course_event_id_for",
    "course_profile_manifest",
    "decode_course_created",
    "decode_course_profile",
    "decode_study_plan",
    "decode_study_plan_set",
    "reduce_course_created",
    "reduce_study_plan_set",
    "register_course_events",
    "study_plan_manifest",
]
