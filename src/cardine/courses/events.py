"""Strict codec and deterministic identity for ``course.created@1``."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from hashlib import sha256

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.course import (
    CourseProfile,
    SourcePolicy,
    TerminologyEntry,
    TerminologyPolicy,
)
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
