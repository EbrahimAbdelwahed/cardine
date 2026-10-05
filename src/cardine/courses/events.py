"""Strict codec and deterministic identity for ``course.created@1``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from cardine.domain.course import (
    CourseProfile,
    SourcePolicy,
    TerminologyEntry,
    TerminologyPolicy,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.events import Actor, DomainEvent, PrincipalKind
from study_agent.domain.identifiers import CorrelationId, CourseId, EventId
from study_agent.state.serialization import canonical_json_bytes

COURSE_CREATED = "course.created"
COURSE_SCHEMA_VERSION = 1

_PROFILE_KEYS = frozenset(
    {
        "id",
        "title",
        "language",
        "exam_date",
        "assessment_styles",
        "learning_goals",
        "source_policy",
        "terminology_policy",
    }
)
_SOURCE_POLICY_KEYS = frozenset({"allowed_roles", "minimum_trust_level"})
_TERMINOLOGY_POLICY_KEYS = frozenset({"entries"})
_TERMINOLOGY_ENTRY_KEYS = frozenset({"concept", "preferred_term"})


@dataclass(frozen=True, slots=True)
class CourseCreated:
    profile: CourseProfile


def _object(value: JsonValue | None, name: str, keys: frozenset[str]) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    actual = frozenset(value)
    if actual != keys:
        raise ValueError(
            f"{name} fields mismatch; missing={sorted(keys - actual)}, "
            f"extra={sorted(actual - keys)}"
        )
    return value


def _text(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _integer(value: JsonValue | None, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"{name} must be an integer")
    return value


def _array(value: JsonValue | None, name: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an array")
    return value


def _text_array(value: JsonValue | None, name: str) -> tuple[str, ...]:
    values = _array(value, name)
    return tuple(_text(item, f"{name}[{index}]") for index, item in enumerate(values))


def course_profile_manifest(profile: CourseProfile) -> JsonObject:
    return {
        "id": str(profile.id),
        "title": profile.title,
        "language": profile.language,
        "exam_date": profile.exam_date.isoformat() if profile.exam_date is not None else None,
        "assessment_styles": profile.assessment_styles,
        "learning_goals": profile.learning_goals,
        "source_policy": {
            "allowed_roles": profile.source_policy.allowed_roles,
            "minimum_trust_level": profile.source_policy.minimum_trust_level,
        },
        "terminology_policy": {
            "entries": tuple(
                {"concept": item.concept, "preferred_term": item.preferred_term}
                for item in profile.terminology_policy.entries
            )
        },
    }


def decode_course_profile(value: JsonValue | None) -> CourseProfile:
    payload = _object(value, "profile", _PROFILE_KEYS)
    raw_date = payload.get("exam_date")
    if raw_date is None:
        exam_date = None
    else:
        text = _text(raw_date, "profile.exam_date")
        try:
            exam_date = date.fromisoformat(text)
        except ValueError as error:
            raise ValueError("profile.exam_date must be an ISO-8601 date") from error
        if exam_date.isoformat() != text:
            raise ValueError("profile.exam_date must be a canonical ISO-8601 date")
    source = _object(payload.get("source_policy"), "profile.source_policy", _SOURCE_POLICY_KEYS)
    terminology = _object(
        payload.get("terminology_policy"),
        "profile.terminology_policy",
        _TERMINOLOGY_POLICY_KEYS,
    )
    entries_list: list[TerminologyEntry] = []
    for index, item in enumerate(
        _array(terminology.get("entries"), "profile.terminology_policy.entries")
    ):
        name = f"profile.terminology_policy.entries[{index}]"
        entry = _object(item, name, _TERMINOLOGY_ENTRY_KEYS)
        entries_list.append(
            TerminologyEntry(
                _text(entry.get("concept"), f"{name}.concept"),
                _text(entry.get("preferred_term"), f"{name}.preferred_term"),
            )
        )
    entries = tuple(entries_list)
    return CourseProfile(
        id=CourseId(_text(payload.get("id"), "profile.id")),
        title=_text(payload.get("title"), "profile.title"),
        language=_text(payload.get("language"), "profile.language"),
        exam_date=exam_date,
        assessment_styles=_text_array(
            payload.get("assessment_styles"), "profile.assessment_styles"
        ),
        learning_goals=_text_array(payload.get("learning_goals"), "profile.learning_goals"),
        source_policy=SourcePolicy(
            _text_array(source.get("allowed_roles"), "profile.source_policy.allowed_roles"),
            _integer(
                source.get("minimum_trust_level"),
                "profile.source_policy.minimum_trust_level",
            ),
        ),
        terminology_policy=TerminologyPolicy(entries),
    )


def decode_course_created(event: DomainEvent) -> CourseCreated:
    if event.event_type != COURSE_CREATED or event.schema_version != COURSE_SCHEMA_VERSION:
        raise ValueError("event envelope does not match course.created@1")
    if event.session_id is not None or event.causation_id is not None:
        raise ValueError("course.created cannot be session-scoped or caused by another event")
    if event.course_sequence != 1:
        raise ValueError("course.created must be the first event in its course stream")
    if not isinstance(event.course_id, CourseId) or not isinstance(event.event_id, EventId):
        raise ValueError("course event identity envelope is not typed")
    if not isinstance(event.correlation_id, CorrelationId):
        raise ValueError("course event correlation envelope is not typed")
    if (
        not isinstance(event.actor, Actor)
        or not isinstance(event.actor.kind, PrincipalKind)
        or event.actor.kind not in (PrincipalKind.HUMAN, PrincipalKind.SERVICE)
    ):
        raise ValueError("course creation requires a trusted human or service actor")
    profile = decode_course_profile(event.payload)
    if profile.id != event.course_id:
        raise ValueError("profile id must match event course id")
    if event.event_id != course_event_id_for(profile):
        raise ValueError("event id does not match the canonical course command")
    return CourseCreated(profile)


def course_command_fingerprint(profile: CourseProfile) -> str:
    return sha256(canonical_json_bytes(course_profile_manifest(profile))).hexdigest()


def course_event_id_for(profile: CourseProfile) -> EventId:
    identity = f"course.created@1\0{profile.id}\0{course_command_fingerprint(profile)}".encode()
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


# ---- course.study_plan_set@1 ------------------------------------------------
# The learner's exam date and study rhythm. Course creation stays immutable;
# this later, explicit decision replaces the profile's exam date and records
# the daily rhythm and objective the plan is built from.

COURSE_STUDY_PLAN_SET = "course.study_plan_set"
STUDY_PLAN_SCHEMA_VERSION = 1
STUDY_PLAN_MIN_DAILY_MINUTES = 5
STUDY_PLAN_MAX_DAILY_MINUTES = 720
STUDY_PLAN_MAX_OBJECTIVE = 240
_STUDY_PLAN_KEYS = frozenset({"exam_date", "daily_minutes", "objective"})
_STUDY_PLAN_ACTORS = (PrincipalKind.HUMAN, PrincipalKind.SERVICE)


@dataclass(frozen=True, slots=True)
class StudyPlan:
    exam_date: date | None
    daily_minutes: int | None
    objective: str | None

    def __post_init__(self) -> None:
        if self.exam_date is not None and type(self.exam_date) is not date:
            raise ValueError("study plan exam_date must be a date")
        if self.daily_minutes is not None and (
            type(self.daily_minutes) is not int
            or not STUDY_PLAN_MIN_DAILY_MINUTES
            <= self.daily_minutes
            <= STUDY_PLAN_MAX_DAILY_MINUTES
        ):
            raise ValueError("study plan daily_minutes is out of range")
        if self.objective is not None and (
            not isinstance(self.objective, str)
            or not self.objective
            or self.objective != self.objective.strip()
            or len(self.objective) > STUDY_PLAN_MAX_OBJECTIVE
        ):
            raise ValueError("study plan objective must be short trimmed text")


@dataclass(frozen=True, slots=True)
class StudyPlanSet:
    plan: StudyPlan


def study_plan_manifest(plan: StudyPlan) -> JsonObject:
    return {
        "exam_date": None if plan.exam_date is None else plan.exam_date.isoformat(),
        "daily_minutes": plan.daily_minutes,
        "objective": plan.objective,
    }


def decode_study_plan(value: JsonValue | None) -> StudyPlan:
    payload = _object(value, "study_plan", _STUDY_PLAN_KEYS)
    raw_date = payload.get("exam_date")
    if raw_date is None:
        exam_date = None
    else:
        text = _text(raw_date, "study_plan.exam_date")
        try:
            exam_date = date.fromisoformat(text)
        except ValueError as error:
            raise ValueError("study_plan.exam_date must be an ISO date") from error
        if exam_date.isoformat() != text:
            raise ValueError("study_plan.exam_date must be canonical YYYY-MM-DD")
    raw_minutes = payload.get("daily_minutes")
    minutes = None if raw_minutes is None else _integer(raw_minutes, "study_plan.daily_minutes")
    raw_objective = payload.get("objective")
    objective = None if raw_objective is None else _text(raw_objective, "study_plan.objective")
    return StudyPlan(exam_date, minutes, objective)


def study_plan_event_id(course_id: CourseId, idempotency_key: str) -> EventId:
    identity = f"{COURSE_STUDY_PLAN_SET}@1\0{course_id}\0{idempotency_key}".encode()
    return EventId(f"event-sha256:{sha256(identity).hexdigest()}")


def decode_study_plan_set(event: DomainEvent) -> StudyPlanSet:
    if (
        event.event_type != COURSE_STUDY_PLAN_SET
        or event.schema_version != STUDY_PLAN_SCHEMA_VERSION
    ):
        raise ValueError("event envelope does not match course.study_plan_set@1")
    if event.session_id is not None or event.causation_id is not None:
        raise ValueError("course.study_plan_set cannot be session-scoped or caused")
    if event.course_sequence < 2:
        raise ValueError("a study plan requires an existing course")
    if (
        not isinstance(event.actor, Actor)
        or not isinstance(event.actor.kind, PrincipalKind)
        or event.actor.kind not in _STUDY_PLAN_ACTORS
    ):
        raise ValueError("a study plan requires a trusted human or service actor")
    return StudyPlanSet(decode_study_plan(event.payload))
