"""Payload-free, process-local observations for one live tutor turn.

The activity store is deliberately separate from :mod:`turn_trace`: it keeps
only a bounded list of UI-safe observations.  Labels and references come from
the closed vocabulary below; targets are canonical titles already selected by
the host.  Learner/model text, prompts, tool arguments, search queries,
citations, and canonical identifiers are rejected before they can enter the
store.  Nothing in this module is durable or emitted to telemetry.
"""

from __future__ import annotations

import contextvars
import re
from collections import OrderedDict
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from datetime import UTC, datetime
from threading import RLock
from types import MappingProxyType
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue

MAX_TURNS = 8
MAX_RECORDS_PER_TURN = 32
MAX_TARGET_CHARS = 80

# The values are product copy, never provider/model/user supplied text.
REF_LABELS: Mapping[str, str] = MappingProxyType({
        "retrieval.lesson": "Leggo",
    "retrieval.search": "Cerco nelle fonti",
    "capability.explain_concept": "Preparo la spiegazione",
    "capability.propose_flashcards": "Preparo le flashcard",
    "capability.assess_understanding": "Preparo la valutazione",
    "capability.analyze_exam_sample": "Analizzo il campione",
    "capability.grade_response": "Valuto la risposta",
    "verification.answer": "Verifico la risposta",
    "course.create": "Registro nel repository",
    "course.list": "Leggo il repository",
    "session.start": "Avvio la sessione",
    "source.ingest": "Registro la fonte",
    "context.get": "Leggo il contesto",
    "recall.get": "Leggo il ripasso",
    "artifact.get": "Leggo gli artefatti",
    "assessment.get": "Leggo la valutazione",
    "evidence.get": "Leggo le evidenze",
})
ACTIVITY_KINDS = frozenset({"tool", "retrieval", "capability", "model", "verification"})
ACTIVITY_STATES = frozenset({"running", "done", "failed"})
ERROR_CODES = frozenset(
    {
        "capability_failed",
        "cancelled",
        "stale",
        "validation_failed",
        "provider_unavailable",
        "provider_protocol_error",
        "tool_failed",
    }
)

# Inputs with these names are never valid canonical UI titles.  This catches
# accidental forwarding of sensitive payloads even when the caller is buggy.
_FORBIDDEN_TARGET = re.compile(
    r"(?:prompt|query|source_id|revision_id|chunk_id|output_fingerprint|quoted_snippet|"
    r"tool[_ ]?args?)\s*=",
    re.IGNORECASE,
)


class _Turn:
    __slots__ = ("keys", "omitted", "records", "sequence", "state")

    def __init__(self) -> None:
        self.records: list[dict[str, JsonValue]] = []
        self.keys: dict[tuple[str, str], int] = {}
        self.omitted = 0
        self.state = "running"
        self.sequence = 0


_CURRENT: contextvars.ContextVar[tuple[TurnActivityStore, str] | None] = (
    contextvars.ContextVar("cardine_turn_activity", default=None)
)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


def _target(value: object) -> str:
    if value is None:
        return ""
    if not isinstance(value, str):
        raise TypeError("activity target must be text")
    value = " ".join(value.split())
    if len(value) > MAX_TARGET_CHARS:
        value = value[:MAX_TARGET_CHARS].rstrip() + "…"
    if _FORBIDDEN_TARGET.search(value):
        raise ValueError("activity target contains forbidden payload text")
    return value


def _validate_ref(ref: object) -> str:
    if not isinstance(ref, str) or ref not in REF_LABELS:
        raise ValueError("activity reference is not in the closed vocabulary")
    return ref


def _validate_kind(kind: object) -> str:
    if not isinstance(kind, str) or kind not in ACTIVITY_KINDS:
        raise ValueError("activity kind is invalid")
    return kind


def _validate_count(count: object) -> int | None:
    if count is None:
        return None
    if type(count) is not int or count < 0:
        raise ValueError("activity count must be a non-negative integer")
    return count


def _validate_status(status: object) -> str:
    if not isinstance(status, str) or status not in ACTIVITY_STATES:
        raise ValueError("activity status is invalid")
    return status


def _validate_error(error_code: object) -> str | None:
    if error_code is None:
        return None
    if not isinstance(error_code, str) or error_code not in ERROR_CODES:
        raise ValueError("activity error code is invalid")
    return error_code


class TurnActivityStore:
    """Own a bounded process-local ring of safe live-turn observations."""

    def __init__(
        self,
        *,
        max_turns: int = MAX_TURNS,
        max_records_per_turn: int = MAX_RECORDS_PER_TURN,
    ) -> None:
        if type(max_turns) is not int or max_turns < 1:
            raise ValueError("activity retention limit must be positive")
        if type(max_records_per_turn) is not int or max_records_per_turn < 1:
            raise ValueError("activity record limit must be positive")
        self._max_turns = max_turns
        self._max_records = max_records_per_turn
        self._turns: OrderedDict[str, _Turn] = OrderedDict()
        self._lock = RLock()

    @contextmanager
    def capture(self, request_id: str) -> Iterator[str]:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request id is required for activity capture")
        with self._lock:
            turn = self._turns.get(request_id)
            if turn is None:
                turn = _Turn()
                self._turns[request_id] = turn
                while len(self._turns) > self._max_turns:
                    self._turns.popitem(last=False)
            else:
                self._turns.move_to_end(request_id)
                if turn.state != "failed":
                    turn.state = "running"
        token = _CURRENT.set((self, request_id))
        try:
            yield request_id
        finally:
            _CURRENT.reset(token)

    def _turn(self, request_id: str) -> _Turn | None:
        return self._turns.get(request_id)

    def _append(self, request_id: str, record: dict[str, JsonValue]) -> tuple[str, int]:
        key = (cast(str, record["ref"]), cast(str, record["target"]))
        turn = self._turn(request_id)
        if turn is None:
            return request_id, -1
        existing = turn.keys.get(key)
        if existing is not None:
            current = turn.records[existing]
            if current.get("status") == "running":
                if record.get("status") != "running":
                    for field in ("status", "ended_at", "error_code", "count"):
                        if field in record and record[field] is not None:
                            current[field] = record[field]
                else:
                    for field in ("count", "error_code"):
                        if field in record and record[field] is not None:
                            current[field] = record[field]
            return request_id, cast(int, current["sequence"])
        if len(turn.records) >= self._max_records:
            turn.omitted += 1
            return request_id, -1
        turn.sequence += 1
        record["sequence"] = turn.sequence
        turn.keys[key] = len(turn.records)
        turn.records.append(record)
        return request_id, turn.sequence

    def begin(
        self,
        *,
        kind: str,
        ref: str,
        target: str = "",
        count: int | None = None,
        label: str | None = None,
    ) -> tuple[str, int] | None:
        kind = _validate_kind(kind)
        ref = _validate_ref(ref)
        target = _target(target)
        count = _validate_count(count)
        if label is not None and label != REF_LABELS[ref]:
            raise ValueError("activity labels are compiled and cannot be supplied")
        now = _now()
        record: dict[str, JsonValue] = {
            "sequence": 0,
            "kind": kind,
            "ref": ref,
            "label": REF_LABELS[ref],
            "target": target,
            "count": count,
            "status": "running",
            "started_at": now,
            "ended_at": None,
            "error_code": None,
        }
        with self._lock:
            current = _CURRENT.get()
            if current is None or current[0] is not self:
                return None
            _request_id, sequence = self._append(current[1], record)
            return None if sequence < 0 else (_request_id, sequence)

    begin_activity = begin

    def finish(
        self,
        token: tuple[str, int] | None,
        *,
        status: str = "done",
        error_code: str | None = None,
        count: int | None = None,
    ) -> None:
        if token is None:
            return
        status = _validate_status(status)
        error_code = _validate_error(error_code)
        count = _validate_count(count)
        request_id, sequence = token
        with self._lock:
            turn = self._turn(request_id)
            if turn is None or sequence < 1:
                return
            record = next(
                (item for item in turn.records if item.get("sequence") == sequence), None
            )
            if record is None:
                return
            if count is not None:
                record["count"] = count
            if record.get("status") != "running":
                return
            record["status"] = status
            record["ended_at"] = _now()
            record["error_code"] = error_code

    finish_activity = finish

    def add_settled(
        self,
        request_id: str | Mapping[str, object],
        value: Mapping[str, object] | None = None,
    ) -> None:
        if value is None:
            current = _CURRENT.get()
            if current is None or current[0] is not self:
                return
            value = cast(Mapping[str, object], request_id)
            request_id = current[1]
        if not isinstance(value, Mapping):
            raise TypeError("settled activity record must be an object")
        kind = _validate_kind(value.get("kind"))
        ref = _validate_ref(value.get("ref"))
        target = _target(value.get("target", ""))
        count = _validate_count(value.get("count"))
        status = _validate_status(value.get("status", "done"))
        if status == "running":
            raise ValueError("settled activity records must be closed")
        error_code = _validate_error(value.get("error_code"))
        label = value.get("label")
        if label is not None and label != REF_LABELS[ref]:
            raise ValueError("activity labels are compiled and cannot be supplied")
        now = _now()
        record: dict[str, JsonValue] = {
            "sequence": 0,
            "kind": kind,
            "ref": ref,
            "label": REF_LABELS[ref],
            "target": target,
            "count": count,
            "status": status,
            "started_at": now,
            "ended_at": now,
            "error_code": error_code,
        }
        with self._lock:
            if request_id not in self._turns:
                return
            self._append(request_id, record)

    def settle(self, request_id: str, *, status: str = "done") -> JsonObject:
        if status not in {"done", "failed"}:
            raise ValueError("turn activity settle status is invalid")
        with self._lock:
            turn = self._turn(request_id)
            if turn is None:
                return self._snapshot_locked(request_id)
            for record in turn.records:
                if record.get("status") == "running":
                    record["status"] = status
                    record["ended_at"] = _now()
                    if status == "failed":
                        record["error_code"] = "capability_failed"
            turn.state = "failed" if status == "failed" else "settled"
            self._turns.move_to_end(request_id)
            return self._snapshot_locked(request_id)

    def _snapshot_locked(self, request_id: str) -> JsonObject:
        turn = self._turn(request_id)
        if turn is None:
            return {"schema_version": 1, "state": "unknown", "records": [], "omitted": 0}
        records = tuple(cast(JsonObject, dict(item)) for item in turn.records)
        return {
            "schema_version": 1,
            "state": turn.state,
            "records": records,
            "omitted": turn.omitted,
        }

    def snapshot(self, request_id: str) -> JsonObject:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request id is required for activity snapshot")
        with self._lock:
            return self._snapshot_locked(request_id)


def begin_activity(
    *,
    kind: str,
    ref: str,
    target: str = "",
    count: int | None = None,
    label: str | None = None,
) -> tuple[str, int] | None:
    current = _CURRENT.get()
    return None if current is None else current[0].begin(
        kind=kind, ref=ref, target=target, count=count, label=label
    )


def finish_activity(
    token: tuple[str, int] | None,
    *,
    status: str = "done",
    error_code: str | None = None,
    count: int | None = None,
) -> None:
    current = _CURRENT.get()
    if current is not None:
        current[0].finish(token, status=status, error_code=error_code, count=count)


def add_settled(record: Mapping[str, object]) -> None:
    current = _CURRENT.get()
    if current is not None:
        current[0].add_settled(current[1], record)


__all__ = [
    "ACTIVITY_KINDS",
    "ERROR_CODES",
    "REF_LABELS",
    "TurnActivityStore",
    "add_settled",
    "begin_activity",
    "finish_activity",
]
