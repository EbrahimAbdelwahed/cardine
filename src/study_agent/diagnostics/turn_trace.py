"""Bounded, in-memory and payload-free traces for repository tutor turns.

The module deliberately accepts a closed event vocabulary.  It cannot record
learner/model text, prompts, evidence content, credentials, cookies, provider
bodies, or arbitrary exception strings.
"""

from __future__ import annotations

import contextvars
import hashlib
import os
import time
from collections import OrderedDict
from collections.abc import AsyncIterator, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from threading import RLock

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports import (
    CancellationToken,
    ModelError,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)

MAX_TURN_TRACES = 24
MAX_EVENTS_PER_TRACE = 64
_MAX_DURATION_MS = 600_000

_PHASES = frozenset(
    {
        "ui.optimistic_insert",
        "ui.ack",
        "ui.render",
        "ui.retry",
        "ui.reload",
        "api.accepted",
        "learner.lookup",
        "learner.persist",
        "model.decision",
        "structured_output.decision",
        "routing.source_intent",
        "retrieval.search",
        "model.grounding",
        "structured_output.grounding",
        "host.run",
        "response.compose",
        "response.persist",
        "api.response",
        "terminal",
    }
)
_OUTCOMES = frozenset(
    {
        "started",
        "accepted",
        "hit",
        "miss",
        "committed",
        "passed",
        "failed",
        "routed",
        "skipped",
        "sufficient",
        "insufficient",
        "completed",
        "terminated",
        "rendered",
        "retried",
        "reconciled",
    }
)
_CATEGORIES = frozenset(
    {
        "authentication",
        "model_unavailable",
        "endpoint_incompatible",
        "rate_limited",
        "timeout",
        "protocol_error",
        "unavailable",
        "cancelled",
        "unsupported_operation",
        "invalid_request",
        "conflict",
        "retryable_conflict",
        "incompatible_runtime",
        "insufficient_evidence",
        "internal",
    }
)
_DETAIL_ENUMS: dict[str, frozenset[str]] = {
    "decision_kind": frozenset(
        {"ask_learner", "start_capability", "invoke_tool", "stop"}
    ),
    "finish_reason": frozenset(
        {"stop", "length", "tool_calls", "cancelled", "content_filter", "unknown"}
    ),
    "host_status": frozenset(
        {
            "completed",
            "terminated",
            "assistant_message",
            "needs_learner_input",
            "suspended",
            "failed",
            "interrupted",
        }
    ),
}
_DETAIL_BOOLS = frozenset(
    {
        "idempotent_retry",
        "source_intent",
        "has_materials",
        "exact_title_match",
        "structured_output",
        "learner_persisted",
    }
)
_DETAIL_INTS = frozenset(
    {
        "expected_sequence",
        "evidence_count",
        "candidate_count",
        "material_count",
        "attempt",
    }
)


@dataclass(slots=True)
class _Trace:
    trace_id: str
    started_unix: int
    started_monotonic: float
    updated_unix: int
    attempt_count: int = 0
    final_status: str = "in_progress"
    learner_persisted: bool = False
    events: list[dict[str, JsonValue]] = field(default_factory=list)


_CURRENT: contextvars.ContextVar[tuple[TurnTraceStore, str] | None] = (
    contextvars.ContextVar("cardine_turn_trace", default=None)
)


class TurnTraceStore:
    """Own a process-local ring of correlated safe tutor-turn records."""

    def __init__(
        self,
        *,
        max_traces: int = MAX_TURN_TRACES,
        max_events_per_trace: int = MAX_EVENTS_PER_TRACE,
    ) -> None:
        if max_traces < 1 or max_events_per_trace < 1:
            raise ValueError("turn trace retention limits must be positive")
        self._max_traces = max_traces
        self._max_events = max_events_per_trace
        self._salt = os.urandom(32)
        self._traces: OrderedDict[str, _Trace] = OrderedDict()
        self._lock = RLock()

    @contextmanager
    def capture(self, request_id: str, expected_sequence: int) -> Iterator[str]:
        trace_id = self.trace_id_for_request(request_id)
        with self._lock:
            trace = self._traces.get(trace_id)
            retry = trace is not None and trace.attempt_count > 0
            if trace is None:
                now = int(time.time())
                trace = _Trace(trace_id, now, time.monotonic(), now)
                self._traces[trace_id] = trace
                while len(self._traces) > self._max_traces:
                    self._traces.popitem(last=False)
            else:
                self._traces.move_to_end(trace_id)
            trace.attempt_count += 1
            trace.final_status = "in_progress"
            attempt = trace.attempt_count
        token = _CURRENT.set((self, trace_id))
        try:
            self.record(
                trace_id,
                "api.accepted",
                "accepted",
                details={
                    "expected_sequence": expected_sequence,
                    "idempotent_retry": retry,
                    "attempt": attempt,
                },
            )
            yield trace_id
        finally:
            _CURRENT.reset(token)

    def trace_id_for_request(self, request_id: str) -> str:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request id is required for tracing")
        digest = hashlib.sha256(self._salt + request_id.encode("utf-8")).hexdigest()
        return f"turn-{digest[:20]}"

    def record(
        self,
        trace_id: str,
        phase: str,
        outcome: str,
        *,
        duration_ms: int | None = None,
        category: str | None = None,
        details: Mapping[str, object] | None = None,
    ) -> None:
        if phase not in _PHASES or outcome not in _OUTCOMES:
            return
        safe_category = category if category in _CATEGORIES else None
        safe_details = _safe_details(details)
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                return
            event: dict[str, JsonValue] = {
                "sequence": len(trace.events) + 1,
                "offset_ms": max(0, int((time.monotonic() - trace.started_monotonic) * 1000)),
                "phase": phase,
                "outcome": outcome,
            }
            if duration_ms is not None:
                event["duration_ms"] = max(0, min(int(duration_ms), _MAX_DURATION_MS))
            if safe_category is not None:
                event["category"] = safe_category
            if safe_details:
                event["details"] = safe_details
            trace.events.append(event)
            if len(trace.events) > self._max_events:
                del trace.events[: len(trace.events) - self._max_events]
                for sequence, retained in enumerate(trace.events, start=1):
                    retained["sequence"] = sequence
            trace.updated_unix = int(time.time())

    def terminal(
        self,
        trace_id: str,
        status: str,
        *,
        learner_persisted: bool,
        category: str | None = None,
    ) -> None:
        outcome = status if status in {"completed", "terminated"} else "failed"
        self.record(
            trace_id,
            "terminal",
            outcome,
            category=category,
            details={"learner_persisted": learner_persisted},
        )
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is not None:
                trace.final_status = status if status in {
                    "completed", "terminated", "failed"
                } else "failed"
                trace.learner_persisted = learner_persisted

    def client_event(self, command: Mapping[str, object]) -> str | None:
        trace_id = command.get("trace_id")
        if not isinstance(trace_id, str):
            request_id = command.get("request_id")
            if not isinstance(request_id, str) or not request_id:
                return None
            trace_id = self.trace_id_for_request(request_id)
        phase = command.get("phase")
        outcome = command.get("outcome")
        if not isinstance(phase, str) or not phase.startswith("ui."):
            return None
        if not isinstance(outcome, str):
            return None
        with self._lock:
            if trace_id not in self._traces:
                now = int(time.time())
                self._traces[trace_id] = _Trace(
                    trace_id, now, time.monotonic(), now
                )
                while len(self._traces) > self._max_traces:
                    self._traces.popitem(last=False)
        self.record(trace_id, phase, outcome)
        return trace_id if trace_id in self._traces else None

    def snapshot(self) -> JsonObject:
        with self._lock:
            traces = tuple(_trace_object(trace) for trace in self._traces.values())
        return {
            "schema_version": 2,
            "latest_trace_id": traces[-1]["trace_id"] if traces else None,
            "turn_traces": traces,
            "retention": {
                "max_traces": self._max_traces,
                "max_events_per_trace": self._max_events,
                "storage": "memory_only",
                "external_telemetry": False,
                "payload_capture": False,
            },
        }


def record_turn_event(
    phase: str,
    outcome: str,
    *,
    duration_ms: int | None = None,
    category: str | None = None,
    details: Mapping[str, object] | None = None,
) -> None:
    current = _CURRENT.get()
    if current is not None:
        current[0].record(
            current[1], phase, outcome,
            duration_ms=duration_ms, category=category, details=details,
        )


def current_turn_trace_id() -> str | None:
    current = _CURRENT.get()
    return None if current is None else current[1]


class _TracedModel:
    def __init__(self, delegate: ModelPort) -> None:
        self._delegate = delegate

    @property
    def capabilities(self):  # type: ignore[no-untyped-def]
        return self._delegate.capabilities

    async def generate(self, request: ModelRequest) -> ModelResponse:
        prompt_id = request.metadata.get("prompt_id")
        phase = "model.grounding" if prompt_id == "explain_concept.v1" else "model.decision"
        started = time.monotonic()
        try:
            response = await self._delegate.generate(request)
        except ModelError as error:
            record_turn_event(
                phase, "failed", duration_ms=_elapsed(started), category=error.code.value
            )
            raise
        except Exception:
            record_turn_event(
                phase, "failed", duration_ms=_elapsed(started), category="internal"
            )
            raise
        record_turn_event(
            phase,
            "completed",
            duration_ms=_elapsed(started),
            details={
                "finish_reason": response.finish_reason.value,
                "structured_output": response.structured_output is not None,
            },
        )
        return response

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        return self._delegate.stream(request)

    async def cancel(self, token: CancellationToken) -> None:
        await self._delegate.cancel(token)


def trace_model(model: ModelPort) -> ModelPort:
    return _TracedModel(model)


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


def _safe_details(details: Mapping[str, object] | None) -> JsonObject:
    safe: dict[str, JsonValue] = {}
    for key, value in (details or {}).items():
        if key in _DETAIL_BOOLS and isinstance(value, bool):
            safe[key] = value
        elif key in _DETAIL_INTS and type(value) is int:
            safe[key] = max(0, min(value, 1_000_000))
        elif key in _DETAIL_ENUMS and isinstance(value, str) and value in _DETAIL_ENUMS[key]:
            safe[key] = value
    return safe


def _trace_object(trace: _Trace) -> JsonObject:
    return {
        "trace_id": trace.trace_id,
        "started_unix": trace.started_unix,
        "updated_unix": trace.updated_unix,
        "attempt_count": trace.attempt_count,
        "final_status": trace.final_status,
        "learner_persisted": trace.learner_persisted,
        "events": tuple(dict(event) for event in trace.events),
    }
