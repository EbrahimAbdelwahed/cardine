"""Structured, attributable tutor memory over canonical session notes."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import cast

from study_agent.domain import (
    CourseId,
    ExecutionContext,
    InteractionId,
    PrincipalKind,
    SessionId,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.events import DomainEvent
from study_agent.ports import EventStore, SessionViewPort
from study_agent.sessions import SESSION_INTERACTION_RECORDED, SessionService
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


class StudyMemoryArchive:
    """Record and search bounded study observations without deriving mastery."""

    def __init__(
        self,
        events: EventStore,
        sessions: SessionViewPort,
        session_service: SessionService,
    ) -> None:
        if not hasattr(events, "read") or not hasattr(sessions, "interactions"):
            raise TypeError("study memory requires canonical event and session views")
        if not isinstance(session_service, SessionService):
            raise TypeError("study memory requires SessionService")
        self._events = events
        self._sessions = sessions
        self._session_service = session_service

    def record_learner_signal(
        self,
        *,
        topic: str,
        summary: str,
        signal: str,
        assistance: str,
        context: ExecutionContext,
        origin_sequence: int,
    ) -> StudyMemoryEntry:
        return self._record(
            kind=StudyMemoryKind.LEARNER_SIGNAL,
            topic=_text(topic, "topic", _MAX_TOPIC_CHARS),
            summary=_text(summary, "summary", _MAX_SUMMARY_CHARS),
            signal=StudyMemorySignal(signal),
            assistance=StudyMemoryAssistance(assistance),
            context=context,
            origin_sequence=origin_sequence,
        )

    def record_topic_covered(
        self,
        *,
        topic: str,
        context: ExecutionContext,
        origin_sequence: int,
    ) -> StudyMemoryEntry:
        return self._record(
            kind=StudyMemoryKind.TOPIC_COVERED,
            topic=_text(topic, "topic", _MAX_TOPIC_CHARS),
            summary=None,
            signal=None,
            assistance=None,
            context=context,
            origin_sequence=origin_sequence,
        )

    def search(
        self,
        course_id: CourseId,
        *,
        query: str | None = None,
        kind: str = "any",
        signal: str = "any",
        limit: int = _MAX_RESULTS,
        through_sequence: int | None = None,
    ) -> tuple[StudyMemoryEntry, ...]:
        if not isinstance(course_id, CourseId):
            raise TypeError("study memory search requires CourseId")
        if query is not None:
            query = _text(query, "query", _MAX_QUERY_CHARS)
        if kind != "any":
            kind = StudyMemoryKind(kind).value
        if signal != "any":
            signal = StudyMemorySignal(signal).value
        if type(limit) is not int or not 1 <= limit <= _MAX_RESULTS:
            raise ValueError("study memory search limit is invalid")
        events = tuple(self._events.read(course_id))
        high_water = events[-1].course_sequence if events else 0
        if through_sequence is None:
            through_sequence = high_water
        if (
            type(through_sequence) is not int
            or through_sequence < 0
            or through_sequence > high_water
        ):
            raise ValueError("study memory high-water bound is invalid")
        terms = () if query is None else tuple(
            dict.fromkeys(token.casefold() for token in _TOKEN.findall(query))
        )
        origins = _human_origins(events)
        entries: list[StudyMemoryEntry] = []
        for event in reversed(events):
            if event.course_sequence > through_sequence:
                continue
            entry = _entry_from_event(event, origins)
            if entry is None:
                continue
            if kind != "any" and entry.kind != kind:
                continue
            if signal != "any" and entry.signal != signal:
                continue
            haystack = f"{entry.topic} {entry.summary or ''}".casefold()
            if terms and not all(term in haystack for term in terms):
                continue
            entries.append(entry)
            if len(entries) == limit:
                break
        return tuple(entries)

    def validated_memory_ids(
        self,
        course_id: CourseId,
        *,
        through_sequence: int | None = None,
    ) -> frozenset[str]:
        """Return canonical interaction IDs safe to hide from ordinary chat."""

        events = tuple(self._events.read(course_id))
        high_water = events[-1].course_sequence if events else 0
        if through_sequence is None:
            through_sequence = high_water
        if (
            type(through_sequence) is not int
            or through_sequence < 0
            or through_sequence > high_water
        ):
            raise ValueError("study memory high-water bound is invalid")
        origins = _human_origins(events)
        return frozenset(
            entry.memory_id
            for event in events
            if event.course_sequence <= through_sequence
            if (entry := _entry_from_event(event, origins)) is not None
        )

    def validated_memory_contents(
        self,
        course_id: CourseId,
        *,
        through_sequence: int | None = None,
    ) -> frozenset[str]:
        """Return only trusted structured note bodies for prompt redaction."""

        ids = self.validated_memory_ids(
            course_id, through_sequence=through_sequence
        )
        return frozenset(
            content
            for event in self._events.read(course_id)
            if event.payload.get("interaction_id") in ids
            if isinstance((content := event.payload.get("content")), str)
        )

    def latest_learner_sequence(
        self,
        course_id: CourseId,
        session_id: SessionId,
        *,
        through_sequence: int | None = None,
    ) -> int:
        """Return the latest canonical learner turn visible to this tool call."""

        events = tuple(self._events.read(course_id))
        high_water = events[-1].course_sequence if events else 0
        if through_sequence is None:
            through_sequence = high_water
        if (
            type(through_sequence) is not int
            or through_sequence < 0
            or through_sequence > high_water
        ):
            raise ValueError("study memory high-water bound is invalid")
        for event in reversed(events):
            if event.course_sequence > through_sequence:
                continue
            if (
                event.event_type == SESSION_INTERACTION_RECORDED
                and event.session_id == session_id
                and event.actor.kind is PrincipalKind.HUMAN
                and event.payload.get("kind") == "human"
            ):
                return event.course_sequence
        raise LookupError("study memory requires a preceding learner turn")

    def _record(
        self,
        *,
        kind: StudyMemoryKind,
        topic: str,
        summary: str | None,
        signal: StudyMemorySignal | None,
        assistance: StudyMemoryAssistance | None,
        context: ExecutionContext,
        origin_sequence: int,
    ) -> StudyMemoryEntry:
        recorded_by = _recorded_by(context)
        origin_id = self._origin(context, origin_sequence)
        content = _encode(
            kind=kind,
            topic=topic,
            summary=summary,
            signal=signal,
            assistance=assistance,
            origin_interaction_id=origin_id,
            origin_sequence=origin_sequence,
            recorded_by=recorded_by,
        )
        record = self._session_service.record_note(context, content)
        events = tuple(self._events.read(context.course_id))
        origins = _human_origins(events)
        for event in reversed(events):
            if (
                event.event_type == SESSION_INTERACTION_RECORDED
                and event.payload.get("interaction_id") == str(record.id)
            ):
                entry = _entry_from_event(event, origins)
                if entry is None:
                    raise RuntimeError("recorded study memory failed canonical validation")
                return entry
        raise RuntimeError("recorded study memory event was not found")

    def _origin(self, context: ExecutionContext, origin_sequence: int) -> InteractionId:
        if context.session_id is None:
            raise ValueError("study memory requires a session")
        if type(origin_sequence) is not int or origin_sequence < 1:
            raise ValueError("study memory origin sequence is invalid")
        self._sessions.get_session(context.course_id, context.session_id)
        for event in self._events.read(context.course_id):
            if event.course_sequence != origin_sequence:
                continue
            if (
                event.event_type != SESSION_INTERACTION_RECORDED
                or event.session_id != context.session_id
                or event.actor.kind is not PrincipalKind.HUMAN
                or event.payload.get("kind") != "human"
            ):
                break
            raw_id = event.payload.get("interaction_id")
            if isinstance(raw_id, str) and raw_id.strip():
                return InteractionId(raw_id)
            break
        raise ValueError("study memory origin must be a human turn in the same session")


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
            cleaned[key] = tuple(
                value for value in values if value not in private_contents
            )
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


def _recorded_by(context: ExecutionContext) -> str:
    if context.principal_kind is not PrincipalKind.SERVICE:
        raise ValueError("study memory writes require service authority")
    try:
        return _PRINCIPALS[context.principal_id]
    except KeyError as error:
        raise ValueError("study memory writer is not trusted") from error


def _encode(
    *,
    kind: StudyMemoryKind,
    topic: str,
    summary: str | None,
    signal: StudyMemorySignal | None,
    assistance: StudyMemoryAssistance | None,
    origin_interaction_id: InteractionId,
    origin_sequence: int,
    recorded_by: str,
) -> str:
    payload = {
        "assistance": None if assistance is None else assistance.value,
        "kind": kind.value,
        "origin_interaction_id": str(origin_interaction_id),
        "origin_sequence": origin_sequence,
        "recorded_by": recorded_by,
        "schema_version": 1,
        "signal": None if signal is None else signal.value,
        "summary": summary,
        "topic": topic,
    }
    return _PREFIX + canonical_json_bytes(payload).decode("utf-8")


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


__all__ = [
    "StudyMemoryArchive",
    "StudyMemoryAssistance",
    "StudyMemoryEntry",
    "StudyMemoryKind",
    "StudyMemorySignal",
    "is_study_memory_note",
    "without_study_memory_summary",
]
