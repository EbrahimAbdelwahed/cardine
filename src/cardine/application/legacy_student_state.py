"""Structured, attributable tutor memory over canonical session notes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from study_agent.domain import (
    InteractionId,
    PrincipalKind,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.events import DomainEvent
from study_agent.sessions import SESSION_INTERACTION_RECORDED
from study_agent.state import canonical_json_bytes

_PREFIX = "study-memory@1:"
_MAX_TOPIC_CHARS = 120
_MAX_SUMMARY_CHARS = 300
_MAX_QUERY_CHARS = 120
_MAX_RESULTS = 8
_TOKEN = re.compile(r"[\wÀ-ÿ]+", re.UNICODE)
_PRINCIPALS = {
    "cardine-tutor-host": "host",
    "study-agent-tutor-tool-host": "tutor_agent",
}


class StudyMemoryKind(StrEnum):
    TOPIC_COVERED = "topic_covered"
    LEARNER_SIGNAL = "learner_signal"


class StudyMemorySignal(StrEnum):
    SELF_REPORTED_DIFFICULTY = "self_reported_difficulty"
    INCORRECT = "incorrect"
    PARTIAL = "partial"
    CORRECT = "correct"
    UNKNOWN = "unknown"


class StudyMemoryAssistance(StrEnum):
    NONE = "none"
    HINT = "hint"
    EXPLANATION = "explanation"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class StudyMemoryEntry:
    memory_id: str
    kind: str
    topic: str
    summary: str | None
    signal: str | None
    assistance: str | None
    origin_sequence: int
    recorded_sequence: int
    recorded_by: str


def is_study_memory_note(content: object) -> bool:
    """Return whether provider context must hide this structured memory payload."""

    return isinstance(content, str) and content.startswith(_PREFIX)


def without_study_memory_summary(
    summary: JsonObject, private_contents: frozenset[str]
) -> JsonObject:
    """Remove private structured notes from capability continuation context."""

    cleaned: dict[str, object] = dict(summary)
    for key in ("grounded_points", "unresolved_notes"):
        values = summary.get(key)
        if isinstance(values, tuple):
            cleaned[key] = tuple(value for value in values if value not in private_contents)
    exchanges = summary.get("recent_exchanges")
    if isinstance(exchanges, tuple):
        cleaned["recent_exchanges"] = tuple(
            exchange
            for exchange in exchanges
            if not (
                isinstance(exchange, Mapping)
                and any(value in private_contents for value in exchange.values())
            )
        )
    return cast(JsonObject, cleaned)


def _entry_from_event(
    event: DomainEvent,
    origins: Mapping[tuple[str, int], str],
) -> StudyMemoryEntry | None:
    if (
        getattr(event, "event_type", None) != SESSION_INTERACTION_RECORDED
        or getattr(getattr(event, "actor", None), "kind", None) is not PrincipalKind.SERVICE
        or getattr(event, "session_id", None) is None
    ):
        return None
    actor = event.actor
    expected_writer = _PRINCIPALS.get(actor.principal_id)
    if expected_writer is None:
        return None
    payload = event.payload
    if payload.get("kind") != "note":
        return None
    content = payload.get("content")
    if not isinstance(content, str) or not is_study_memory_note(content):
        return None
    try:
        raw_bytes = content[len(_PREFIX) :].encode("utf-8")
        decoded = json.loads(raw_bytes)
        if not isinstance(decoded, dict) or canonical_json_bytes(decoded) != raw_bytes:
            return None
        if set(decoded) != {
            "assistance",
            "kind",
            "origin_interaction_id",
            "origin_sequence",
            "recorded_by",
            "schema_version",
            "signal",
            "summary",
            "topic",
        }:
            return None
        kind = StudyMemoryKind(decoded["kind"])
        topic = _text(decoded["topic"], "topic", _MAX_TOPIC_CHARS)
        origin_id = InteractionId(decoded["origin_interaction_id"])
        origin_sequence = decoded["origin_sequence"]
        if (
            decoded["schema_version"] != 1
            or decoded["recorded_by"] != expected_writer
            or type(origin_sequence) is not int
            or not 1 <= origin_sequence < event.course_sequence
            or origins.get((str(event.session_id), origin_sequence)) != str(origin_id)
        ):
            return None
        if kind is StudyMemoryKind.TOPIC_COVERED:
            if any(decoded[key] is not None for key in ("summary", "signal", "assistance")):
                return None
            summary = signal = assistance = None
        else:
            summary = _text(decoded["summary"], "summary", _MAX_SUMMARY_CHARS)
            signal = StudyMemorySignal(decoded["signal"]).value
            assistance = StudyMemoryAssistance(decoded["assistance"]).value
        memory_id = payload.get("interaction_id")
        if not isinstance(memory_id, str) or not memory_id.strip():
            return None
        del origin_id
        return StudyMemoryEntry(
            memory_id,
            kind.value,
            topic,
            summary,
            signal,
            assistance,
            origin_sequence,
            event.course_sequence,
            expected_writer,
        )
    except (KeyError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        return None


def _human_origins(events: tuple[DomainEvent, ...]) -> dict[tuple[str, int], str]:
    origins: dict[tuple[str, int], str] = {}
    for event in events:
        if (
            event.event_type != SESSION_INTERACTION_RECORDED
            or event.session_id is None
            or event.actor.kind is not PrincipalKind.HUMAN
            or event.payload.get("kind") != "human"
        ):
            continue
        interaction_id = event.payload.get("interaction_id")
        if isinstance(interaction_id, str) and interaction_id.strip():
            origins[(str(event.session_id), event.course_sequence)] = interaction_id
    return origins


def _text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{name} must be text")
    normalized = " ".join(value.split())
    if not normalized or len(normalized) > maximum:
        raise ValueError(f"{name} is invalid")
    return normalized


def legacy_entries(events: tuple[DomainEvent, ...]) -> tuple[StudyMemoryEntry, ...]:
    origins = _human_origins(events)
    return tuple(entry for event in events if (entry := _entry_from_event(event, origins)))
