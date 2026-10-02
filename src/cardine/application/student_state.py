"""One append-only student journal shared by the tutor and UI.

The historical filename is student-state.json; its format is JSON Lines.
The local study repository belongs to one student; records are course-scoped.
No mastery estimates, projections, indexes or scheduling live here.
"""

from __future__ import annotations

import fcntl
import json
import os
import stat
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import cast

from cardine.application.legacy_student_state import legacy_entries
from study_agent.domain import CourseId, ExecutionContext, PrincipalKind, SessionId
from study_agent.domain._validation import JsonObject, freeze_object
from study_agent.ports import EventStore, SessionViewPort
from study_agent.ports.clock import ClockPort
from study_agent.sessions import SESSION_INTERACTION_RECORDED
from study_agent.sessions.service import IdempotencyConflictError
from study_agent.state import canonical_json_bytes

_KINDS = {"topic_covered", "learner_signal", "context_recorded", "assessment_activity"}
_SIGNALS = {"self_reported_difficulty", "incorrect", "partial", "correct", "unknown"}
_ASSISTANCE = {"none", "hint", "explanation", "unknown"}
_WRITERS = {"cardine-tutor-host": "host", "study-agent-tutor-tool-host": "tutor_agent"}
_MAX_LINE = 16_384


@dataclass(frozen=True, slots=True)
class StudentStateEvent:
    value: JsonObject

    @property
    def event_id(self) -> str:
        return str(self.value["event_id"])

    @property
    def kind(self) -> str:
        return str(self.value["kind"])

    @property
    def topic(self) -> str:
        return str(self.value["topic"])

    @property
    def summary(self) -> str | None:
        return cast(str | None, self.value["summary"])

    @property
    def signal(self) -> str | None:
        return cast(str | None, self.value["signal"])

    @property
    def assistance(self) -> str | None:
        return cast(str | None, self.value["assistance"])

    @property
    def origin_sequence(self) -> int:
        return cast(int, self.value["origin_sequence"])

    @property
    def sequence(self) -> int:
        return cast(int, self.value["sequence"])

    @property
    def recorded_by(self) -> str:
        return str(self.value["recorded_by"])


@dataclass(frozen=True, slots=True)
class StudentStateSnapshot:
    course_id: CourseId
    sequence: int
    entries: tuple[StudentStateEvent, ...]

    def to_json(self, *, limit: int = 100, before_sequence: int | None = None) -> JsonObject:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("student journal limit must be in 1..100")
        if before_sequence is not None and (
            type(before_sequence) is not int or before_sequence < 1
        ):
            raise ValueError("student journal cursor must be positive")
        selected = tuple(
            e for e in self.entries if before_sequence is None or e.sequence < before_sequence
        )
        page = selected[-limit:]
        return {
            "schema_version": 1,
            "course_id": str(self.course_id),
            "sequence": self.sequence,
            "entries": tuple(e.value for e in reversed(page)),
            "total_entries": len(self.entries),
            "has_more": len(selected) > len(page),
            "next_cursor": page[0].sequence if len(selected) > len(page) else None,
        }


class StudentStateService:
    def __init__(
        self,
        path: Path,
        events: EventStore,
        sessions: SessionViewPort,
        clock: ClockPort,
        *,
        verify_binding: Callable[[], None] | None = None,
        directory_descriptor: Callable[[], int] | None = None,
    ) -> None:
        self.path = path
        self._events = events
        self._sessions = sessions
        self._clock = clock
        self._verify_binding = verify_binding
        self._directory_descriptor = directory_descriptor

    def get(self, course_id: CourseId) -> StudentStateSnapshot:
        if not isinstance(course_id, CourseId):
            raise TypeError("student journal requires CourseId")
        with self._open(write=False) as stream:
            records = () if stream is None else self._read(stream)
        return StudentStateSnapshot(
            course_id,
            len(records),
            tuple(StudentStateEvent(r) for r in records if r["course_id"] == str(course_id)),
        )

    def search(
        self,
        course_id: CourseId,
        *,
        query: str | None = None,
        kind: str = "any",
        signal: str = "any",
        limit: int = 8,
        through_sequence: int | None = None,
    ) -> tuple[StudentStateEvent, ...]:
        if type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError("student journal limit must be in 1..100")
        if kind not in _KINDS | {"any"} or signal not in _SIGNALS | {"any"}:
            raise ValueError("invalid student journal filter")
        terms = () if query is None else _text(query, 120).casefold().split()
        return tuple(
            e
            for e in reversed(self.get(course_id).entries)
            if (kind == "any" or e.kind == kind)
            and (signal == "any" or e.signal == signal)
            and (through_sequence is None or e.origin_sequence <= through_sequence)
            and all(t in f"{e.topic} {e.summary or ''}".casefold() for t in terms)
        )[:limit]

    def record_learner_signal(
        self,
        *,
        topic: str,
        summary: str,
        signal: str,
        assistance: str,
        context: ExecutionContext,
        origin_sequence: int,
    ) -> StudentStateEvent:
        if signal not in _SIGNALS or assistance not in _ASSISTANCE:
            raise ValueError("invalid learner observation")
        return self._record(
            "learner_signal", topic, summary, signal, assistance, context, origin_sequence
        )

    def record_topic_covered(
        self, *, topic: str, context: ExecutionContext, origin_sequence: int
    ) -> StudentStateEvent:
        return self._record("topic_covered", topic, None, None, None, context, origin_sequence)

    def _record(
        self,
        kind: str,
        topic: str,
        summary: str | None,
        signal: str | None,
        assistance: str | None,
        context: ExecutionContext,
        origin_sequence: int,
    ) -> StudentStateEvent:
        if context.session_id is None or context.idempotency_key is None:
            raise ValueError("student journal writes require session and idempotency key")
        if context.principal_kind is PrincipalKind.HUMAN:
            writer = "student"
        elif context.principal_kind is PrincipalKind.SERVICE and context.principal_id in _WRITERS:
            writer = _WRITERS[context.principal_id]
        else:
            raise ValueError("student journal writer is not trusted")
        self._sessions.get_session(context.course_id, context.session_id)
        origins = tuple(
            e
            for e in self._events.read(context.course_id)
            if e.course_sequence == origin_sequence
            and e.session_id == context.session_id
            and e.event_type == SESSION_INTERACTION_RECORDED
            and e.actor.kind is PrincipalKind.HUMAN
            and e.payload.get("kind") == "human"
        )
        if (
            not (context.principal_kind is PrincipalKind.HUMAN and origin_sequence == 0)
            and len(origins) != 1
        ):
            raise ValueError("student journal origin must be a human turn in the same session")
        identity = canonical_json_bytes(
            {
                "course": str(context.course_id),
                "session": str(context.session_id),
                "writer": context.principal_id,
                "key": context.idempotency_key,
                "kind": kind,
            }
        )
        return self._append(
            {
                "event_id": "student-" + sha256(identity).hexdigest(),
                "kind": kind,
                "course_id": str(context.course_id),
                "session_id": str(context.session_id),
                "topic": _text(topic, 120),
                "summary": None if summary is None else _text(summary, 500),
                "signal": signal,
                "assistance": assistance,
                "origin_sequence": origin_sequence,
                "recorded_by": writer,
                "reference": str(origins[0].event_id)
                if origins
                else "ui:" + context.idempotency_key,
                "occurred_at": self._clock.now().isoformat(),
            }
        )

    def latest_learner_sequence(
        self, course_id: CourseId, session_id: SessionId, *, through_sequence: int | None = None
    ) -> int:
        for event in reversed(tuple(self._events.read(course_id))):
            if (through_sequence is None or event.course_sequence <= through_sequence) and (
                event.session_id == session_id
                and event.event_type == SESSION_INTERACTION_RECORDED
                and event.actor.kind is PrincipalKind.HUMAN
                and event.payload.get("kind") == "human"
            ):
                return event.course_sequence
        raise LookupError("student journal requires a preceding learner turn")

    def import_history(self, course_id: CourseId) -> StudentStateSnapshot:
        """Idempotently copy historical observations and assessment facts; never edit SQLite.

        Called explicitly by the UI and after study mutations so assessment
        activity is recovered even if a prior process stopped before journal append.
        """
        events = tuple(self._events.read(course_id))
        notes = {e.recorded_sequence: e for e in legacy_entries(events)}
        known = {e.event_id for e in self.get(course_id).entries}
        for event in events:
            if "import-" + str(event.event_id) in known:
                continue
            note = notes.get(event.course_sequence)
            if note is not None:
                kind, topic, summary, signal, assistance, origin, writer = (
                    note.kind,
                    note.topic,
                    note.summary,
                    note.signal,
                    note.assistance,
                    note.origin_sequence,
                    note.recorded_by,
                )
            elif event.event_type.startswith("assessment."):
                kind, topic = "assessment_activity", "Verifica"
                summary = event.event_type.removeprefix("assessment.")
                # References to canonical facts, without answer keys or rubric.
                refs = {
                    k: v
                    for k, v in event.payload.items()
                    if k
                    in {
                        "attempt_id",
                        "grade_id",
                        "presentation_id",
                        "status",
                        "score",
                        "supersedes_grade_id",
                    }
                }
                summary += " " + canonical_json_bytes(refs).decode()
                signal = assistance = None
                origin, writer = event.course_sequence, "host"
            elif event.event_type == "study_context.statement_recorded":
                kind, topic = "context_recorded", str(event.payload.get("kind", "Contesto"))
                summary = str(event.payload.get("value", ""))[:500] or None
                signal = assistance = None
                origin, writer = event.course_sequence, "student"
            else:
                continue
            self._append(
                {
                    "event_id": "import-" + str(event.event_id),
                    "kind": kind,
                    "course_id": str(course_id),
                    "session_id": str(event.session_id),
                    "topic": topic,
                    "summary": summary,
                    "signal": signal,
                    "assistance": assistance,
                    "origin_sequence": origin,
                    "recorded_by": writer,
                    "reference": str(event.event_id),
                    "occurred_at": event.occurred_at.isoformat(),
                }
            )
        return self.get(course_id)

    def validated_memory_ids(
        self, course_id: CourseId, *, through_sequence: int | None = None
    ) -> frozenset[str]:
        return frozenset(
            e.memory_id
            for e in legacy_entries(tuple(self._events.read(course_id)))
            if through_sequence is None or e.recorded_sequence <= through_sequence
        )

    def validated_memory_contents(
        self, course_id: CourseId, *, through_sequence: int | None = None
    ) -> frozenset[str]:
        ids = self.validated_memory_ids(course_id, through_sequence=through_sequence)
        return frozenset(
            c
            for e in self._events.read(course_id)
            if e.payload.get("interaction_id") in ids
            and isinstance(c := e.payload.get("content"), str)
        )

    def _append(self, value: dict[str, object]) -> StudentStateEvent:
        with self._open(write=True) as stream:
            assert stream is not None
            records = self._read(stream)
            for record in records:
                if record["event_id"] == value["event_id"]:
                    expected = {
                        k: v
                        for k, v in record.items()
                        if k not in {"sequence", "schema_version", "occurred_at"}
                    }
                    incoming = {k: v for k, v in value.items() if k != "occurred_at"}
                    if expected != incoming:
                        raise IdempotencyConflictError("student journal retry changed content")
                    return StudentStateEvent(record)
            record = freeze_object(
                {**cast(JsonObject, value), "sequence": len(records) + 1, "schema_version": 1}
            )
            data = canonical_json_bytes(record) + b"\n"
            if len(data) > _MAX_LINE:
                raise ValueError("student journal record is too large")
            _decode_record(data, len(records) + 1, {str(r["event_id"]) for r in records})
            view = memoryview(data)
            while view:
                count = os.write(stream, view)
                if count <= 0:
                    raise OSError("student journal append failed")
                view = view[count:]
            os.fsync(stream)
            return StudentStateEvent(record)

    @contextmanager
    def _open(self, *, write: bool) -> Iterator[int | None]:
        if self._verify_binding is not None:
            self._verify_binding()
        directory = (
            os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
            if self._directory_descriptor is None
            else self._directory_descriptor()
        )
        descriptor = None
        try:
            flags = (
                os.O_NOFOLLOW
                | os.O_NONBLOCK
                | (os.O_RDWR | os.O_APPEND | os.O_CREAT if write else os.O_RDONLY)
            )
            try:
                descriptor = os.open(self.path.name, flags, 0o600, dir_fd=directory)
            except FileNotFoundError:
                if write:
                    raise
                yield None
                return
            if not stat.S_ISREG(os.fstat(descriptor).st_mode) or os.fstat(descriptor).st_nlink != 1:
                raise ValueError("student journal must be a regular, unlinked file")
            fcntl.flock(descriptor, fcntl.LOCK_EX if write else fcntl.LOCK_SH)
            if self._verify_binding is not None:
                self._verify_binding()
            if os.stat(self.path.name, dir_fd=directory, follow_symlinks=False).st_ino != (
                os.fstat(descriptor).st_ino
            ):
                raise ValueError("student journal binding changed")
            yield descriptor
            if write:
                os.fsync(directory)
        finally:
            if descriptor is not None:
                os.close(descriptor)
            os.close(directory)

    @staticmethod
    def _read(descriptor: int) -> tuple[JsonObject, ...]:
        os.lseek(descriptor, 0, os.SEEK_SET)
        records: list[JsonObject] = []
        seen: set[str] = set()
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            while line := stream.readline(_MAX_LINE + 1):
                if len(line) > _MAX_LINE or not line.endswith(b"\n"):
                    raise ValueError(
                        "student journal is incomplete or corrupt; preserve it for recovery"
                    )
                record = _decode_record(line, len(records) + 1, seen)
                records.append(record)
                seen.add(str(record["event_id"]))
        return tuple(records)


def _text(value: str, maximum: int) -> str:
    if not isinstance(value, str) or not (value := " ".join(value.split())) or len(value) > maximum:
        raise ValueError("student journal text is empty or too long")
    return value


def _decode_record(line: bytes, expected_sequence: int, seen: set[str]) -> JsonObject:
    raw = json.loads(line)
    if (
        not isinstance(raw, dict)
        or type(raw.get("schema_version")) is not int
        or raw.get("schema_version") != 1
        or type(raw.get("sequence")) is not int
        or raw["sequence"] != expected_sequence
        or set(raw)
        != {
            "schema_version",
            "sequence",
            "event_id",
            "kind",
            "course_id",
            "session_id",
            "topic",
            "summary",
            "signal",
            "assistance",
            "origin_sequence",
            "recorded_by",
            "reference",
            "occurred_at",
        }
        or not isinstance(raw["kind"], str)
        or raw["kind"] not in _KINDS
    ):
        raise ValueError("student journal record is corrupt")
    if canonical_json_bytes(raw) + b"\n" != line:
        raise ValueError("student journal record is not canonical JSON")
    for name in ("event_id", "course_id", "session_id", "reference", "occurred_at"):
        _text(raw[name], 256)
    _text(raw["topic"], 120)
    if raw["summary"] is not None:
        _text(raw["summary"], 500)
    if (
        type(raw["origin_sequence"]) is not int
        or raw["origin_sequence"] < 0
        or raw["recorded_by"] not in ("student", "host", "tutor_agent")
        or raw["signal"] not in (*_SIGNALS, None)
        or raw["assistance"] not in (*_ASSISTANCE, None)
        or datetime.fromisoformat(raw["occurred_at"]).utcoffset() is None
        or raw["event_id"] in seen
    ):
        raise ValueError("student journal record is corrupt")
    return freeze_object(raw)
