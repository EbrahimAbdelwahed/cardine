"""Pure additive course-profile projection."""

from __future__ import annotations

from collections.abc import Mapping

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import DomainEvent
from study_agent.state import EventRegistry

from .events import (
    COURSE_CREATED,
    COURSE_SCHEMA_VERSION,
    CourseCreated,
    course_profile_manifest,
    decode_course_created,
)


def reduce_course_created(
    state: JsonObject, _: DomainEvent, payload: CourseCreated
) -> Mapping[str, JsonValue]:
    manifest = course_profile_manifest(payload.profile)
    existing = state.get("course")
    if existing is not None:
        raise ValueError("course profile already exists")
    return {**state, "course": manifest}


def register_course_events(registry: EventRegistry) -> None:
    registry.register_event(
        COURSE_CREATED, COURSE_SCHEMA_VERSION, decode_course_created, reduce_course_created
    )
