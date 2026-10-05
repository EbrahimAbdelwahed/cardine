"""Pure additive course-profile projection."""

from __future__ import annotations

from collections.abc import Mapping

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import DomainEvent
from study_agent.state import EventRegistry

from .events import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    COURSE_STUDY_PLAN_SET,
    STUDY_PLAN_SCHEMA_VERSION,
    CourseCreated,
    StudyPlanSet,
    course_profile_manifest,
    decode_course_created,
    decode_study_plan_set,
    study_plan_manifest,
)


def reduce_course_created(
    state: JsonObject, _: DomainEvent, payload: CourseCreated
) -> Mapping[str, JsonValue]:
    manifest = course_profile_manifest(payload.profile)
    existing = state.get("course")
    if existing is not None:
        raise ValueError("course profile already exists")
    return {**state, "course": manifest}


def reduce_study_plan_set(
    state: JsonObject, _: DomainEvent, payload: StudyPlanSet
) -> Mapping[str, JsonValue]:
    course = state.get("course")
    if not isinstance(course, Mapping):
        raise ValueError("a study plan requires an existing course profile")
    plan = study_plan_manifest(payload.plan)
    return {
        **state,
        "course": {**course, "exam_date": plan["exam_date"]},
        "study_plan": plan,
    }


def register_course_events(registry: EventRegistry) -> None:
    registry.register_event(
        COURSE_CREATED, COURSE_SCHEMA_VERSION, decode_course_created, reduce_course_created
    )
    registry.register_event(
        COURSE_STUDY_PLAN_SET,
        STUDY_PLAN_SCHEMA_VERSION,
        decode_study_plan_set,
        reduce_study_plan_set,
    )
