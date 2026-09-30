"""Bounded, in-memory diagnostics for tutor decisions and operational failures.

Only the opaque correlation id and a bounded structural decision trajectory are kept.
Learner/model text, prompts, arguments, sources, exception messages, and HTTP
payloads never enter this store. Operational metadata uses a closed vocabulary.
"""

from __future__ import annotations

import contextvars
import hashlib
import re
import socket
import ssl
import time
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from threading import RLock
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports.model import ModelErrorCode

MAX_TURN_TRACES = 24
MAX_STEPS_PER_TRACE = 4
MAX_OPERATIONS_PER_TRACE = 32
_PHASES = frozenset(
    {
        "application_turn",
        "host_decision",
        "model_decision",
        "model_generation",
        "provider_http",
        "capability_start",
        "capability_resume",
    }
)
_ERROR_CODES = frozenset(code.value for code in ModelErrorCode) | {"tutor_configuration"}
_OUTCOMES = frozenset(
    {
        "completed",
        "failed",
        "terminated",
        "stale",
        "suspended",
        "cancelled",
        "assistant_message",
        "needs_learner_input",
        "in_progress",
        "budget_exhausted",
        "stopped",
    }
)
_EXCEPTION_TYPES = frozenset(
    {
        "TypeError",
        "ValueError",
        "RuntimeError",
        "AttributeError",
        "KeyError",
        "IndexError",
        "ImportError",
        "OSError",
        "SSLError",
        "SSLCertVerificationError",
        "URLError",
        "TimeoutError",
        "ConnectionError",
        "ConnectionRefusedError",
        "gaierror",
    }
)


@dataclass(slots=True)
class _Trace:
    trace_id: str
    decision: JsonObject | None = None
    steps: tuple[JsonObject, ...] = ()
    status: str = "running"
    attempts: int = 0
    operations: tuple[JsonObject, ...] = ()
    operation_sequence: int = 0
    omitted_operations: int = 0
    outcome: str | None = None


def _attribute(error: BaseException, name: str) -> object:
    try:
        return getattr(error, name, None)
    except Exception:
        return None


def _safe_error_code(error: BaseException) -> str | None:
    for attribute in ("code", "failure_reason", "diagnostic_code"):
        code = _attribute(error, attribute)
        if isinstance(code, str):
            if code in _ERROR_CODES:
                return code
            if code.startswith("tutor_") and code[6:] in _ERROR_CODES:
                return code[6:]
    return None


def _error_kind(error: BaseException) -> str:
    # Transport adapters may wrap urllib errors. Inspect only exception types,
    # never messages, headers, URLs, or response bodies; bound malformed chains.
    seen: set[int] = set()
    pending = [error]
    while pending and len(seen) < 8:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))
        if isinstance(current, ssl.SSLCertVerificationError):
            return "tls_certificate"
        if isinstance(current, ssl.SSLError):
            return "tls_error"
        if isinstance(current, socket.gaierror):
            return "dns_error"
        if isinstance(current, TimeoutError):
            return "timeout"
        if isinstance(current, ConnectionRefusedError):
            return "connection_refused"
        if isinstance(current, ConnectionError):
            return "connection_error"
        for nested in (current.__cause__, current.__context__, _attribute(current, "reason")):
            if isinstance(nested, BaseException):
                pending.append(nested)
    return "classified_error" if _safe_error_code(error) else "internal_error"


def _error_location(error: BaseException) -> str | None:
    """Last owned source frame, with no path, source line, or local variables."""
    location = None
    frame = error.__traceback__
    while frame is not None:
        module = frame.tb_frame.f_globals.get("__name__")
        name = frame.tb_frame.f_code.co_name
        if (
            isinstance(module, str)
            and module.startswith(("cardine.", "study_agent."))
            and len(module) <= 120
            and re.fullmatch(r"[a-zA-Z0-9_.]+", module)
            and len(name) <= 80
            and re.fullmatch(r"[a-zA-Z0-9_]+", name)
        ):
            location = f"{module}:{name}:{frame.tb_lineno}"
        frame = frame.tb_next
    return location


class TraceOperation:
    """One span; the only optional transport detail is a validated HTTP status."""

    def __init__(self) -> None:
        self._http_status: int | None = None
        self._outcome: str | None = None
        self._error_code: str | None = None

    def observe_http_status(self, status: int) -> None:
        if type(status) is int and 100 <= status <= 599:
            self._http_status = status

    def observe_outcome(self, outcome: str, failure_reason: str | None = None) -> None:
        if outcome in _OUTCOMES:
            self._outcome = outcome
        if failure_reason in _ERROR_CODES:
            self._error_code = failure_reason


_CURRENT: contextvars.ContextVar[tuple[TurnTraceStore, str] | None] = contextvars.ContextVar(
    "cardine_turn_trace", default=None
)


class TurnTraceStore:
    """Own one process-local ring for correlated decisions and safe operations."""

    def __init__(
        self,
        *,
        max_traces: int = MAX_TURN_TRACES,
        # Compatibility with callers of the old event tracer.
        max_events_per_trace: int = 1,
    ) -> None:
        del max_events_per_trace
        if max_traces < 1:
            raise ValueError("turn trace retention limit must be positive")
        self._max_traces = max_traces
        self._traces: OrderedDict[str, _Trace] = OrderedDict()
        self._lock = RLock()

    @contextmanager
    def capture(self, request_id: str, expected_sequence: int) -> Iterator[str]:
        del expected_sequence
        trace_id = self.trace_id_for_request(request_id)
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                trace = _Trace(trace_id)
                self._traces[trace_id] = trace
                while len(self._traces) > self._max_traces:
                    self._traces.popitem(last=False)
            else:
                self._traces.move_to_end(trace_id)
                # Preserve the last validated decision through idempotent
                # retries that return before invoking the model. A genuinely
                # new decision replaces it in ``record_decision``.
            trace.attempts += 1
            trace.status = "running"
            trace.outcome = None
        token = _CURRENT.set((self, trace_id))
        try:
            with self.operation(trace_id, "application_turn") as operation:
                yield trace_id
                with self._lock:
                    if trace.outcome is not None:
                        operation.observe_outcome(trace.outcome)
        finally:
            _CURRENT.reset(token)

    def record_outcome(self, trace_id: str, outcome: str) -> None:
        if outcome not in _OUTCOMES:
            return
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is not None:
                trace.outcome = outcome

    @contextmanager
    def operation(self, trace_id: str, phase: str) -> Iterator[TraceOperation]:
        if phase not in _PHASES:
            raise ValueError("unknown trace phase")
        operation = TraceOperation()
        started = time.monotonic()
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is None:
                sequence = -1
            else:
                trace.operation_sequence += 1
                sequence = trace.operation_sequence
                record: JsonObject = {
                    "sequence": sequence,
                    "attempt": trace.attempts,
                    "phase": phase,
                    "status": "running",
                    "started_at": datetime.now(UTC).isoformat(timespec="milliseconds"),
                }
                trace.operations = (*trace.operations, record)[-MAX_OPERATIONS_PER_TRACE:]
                if sequence > MAX_OPERATIONS_PER_TRACE:
                    trace.omitted_operations += 1
        error: BaseException | None = None
        try:
            yield operation
        except BaseException as caught:
            error = caught
            raise
        finally:
            status = (
                "failed"
                if error is not None
                or operation._outcome
                in {
                    "failed",
                    "terminated",
                    "stale",
                    "cancelled",
                    "budget_exhausted",
                }
                or (operation._http_status is not None and operation._http_status >= 400)
                else "completed"
            )
            update: dict[str, JsonValue] = {
                "status": status,
                "duration_ms": max(0, round((time.monotonic() - started) * 1000)),
            }
            if operation._http_status is not None:
                update["http_status"] = operation._http_status
            if operation._outcome is not None:
                update["outcome"] = operation._outcome
            if operation._error_code is not None:
                update["error_code"] = operation._error_code
            if error is not None:
                update["error_kind"] = _error_kind(error)
                name = type(error).__name__
                update["error_type"] = (
                    name
                    if name in _EXCEPTION_TYPES
                    else "classified_error"
                    if _safe_error_code(error)
                    else "Exception"
                )
                location = _error_location(error)
                if location is not None:
                    update["error_location"] = location
                code = _safe_error_code(error)
                if code is not None:
                    update["error_code"] = code
            with self._lock:
                trace = self._traces.get(trace_id)
                if trace is not None:
                    trace.operations = tuple(
                        {**record, **update} if record["sequence"] == sequence else record
                        for record in trace.operations
                    )
                    if phase == "application_turn":
                        trace.status = status

    def trace_id_for_request(self, request_id: str) -> str:
        if not isinstance(request_id, str) or not request_id:
            raise ValueError("request id is required for tracing")
        digest = hashlib.sha256(request_id.encode("utf-8")).hexdigest()
        return f"turn-{digest[:20]}"

    def record_decision(self, trace_id: str, decision: object) -> None:
        """Record one locally validated typed decision, and nothing else."""

        # Import lazily: ``hosts`` composes the source-grounding adapter which
        # itself imports this module.
        from cardine.hosts.contracts import (
            AnswerDialogueDecision,
            AskLearnerDecision,
            AssistantMessageDecision,
            InvokeToolDecision,
            StartCapabilityDecision,
            StopDecision,
        )

        if not isinstance(
            decision,
            (
                StartCapabilityDecision,
                AnswerDialogueDecision,
                AskLearnerDecision,
                AssistantMessageDecision,
                InvokeToolDecision,
                StopDecision,
            ),
        ):
            return
        kind = getattr(getattr(decision, "kind", None), "value", None)
        if not isinstance(kind, str):
            return
        payload: dict[str, JsonValue] = {"kind": kind}
        if kind == "stop":
            reason = getattr(getattr(decision, "reason", None), "value", None)
            if not isinstance(reason, str):
                return
            payload["reason"] = reason
        elif kind == "invoke_tool":
            name = getattr(decision, "tool_name", None)
            if not isinstance(name, str):
                return
            payload["tool_name"] = name
        elif kind == "start_capability":
            capability_id = getattr(decision, "capability_id", None)
            if not isinstance(capability_id, str):
                return
            payload["capability_id"] = capability_id
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is not None:
                trace.decision = payload
                trace.steps = (*trace.steps, cast(JsonObject, dict(payload)))[-MAX_STEPS_PER_TRACE:]
                self._traces.move_to_end(trace_id)

    def snapshot(self) -> JsonObject:
        with self._lock:
            traces: tuple[JsonObject, ...] = tuple(
                cast(
                    JsonObject,
                    {
                        "trace_id": trace.trace_id,
                        "decision": None if trace.decision is None else dict(trace.decision),
                        "steps": tuple(cast(JsonObject, dict(step)) for step in trace.steps),
                        "status": trace.status,
                        "attempts": trace.attempts,
                        "operations": tuple(dict(operation) for operation in trace.operations),
                        "omitted_operations": trace.omitted_operations,
                    },
                )
                for trace in self._traces.values()
            )
            latest = next(
                (trace.trace_id for trace in reversed(self._traces.values())),
                None,
            )
        return {
            "schema_version": 5,
            "latest_trace_id": latest,
            "turn_traces": traces,
            "retention": {
                "max_traces": self._max_traces,
                "storage": "memory_only",
                "external_telemetry": False,
                "payload_capture": False,
                "max_steps_per_trace": MAX_STEPS_PER_TRACE,
                "max_operations_per_trace": MAX_OPERATIONS_PER_TRACE,
            },
        }


def record_turn_decision(decision: object) -> None:
    current = _CURRENT.get()
    if current is not None:
        current[0].record_decision(current[1], decision)


def current_turn_trace_id() -> str | None:
    current = _CURRENT.get()
    return None if current is None else current[1]


@contextmanager
def trace_operation(phase: str) -> Iterator[TraceOperation]:
    current = _CURRENT.get()
    if current is None:
        yield TraceOperation()
    else:
        with current[0].operation(current[1], phase) as operation:
            yield operation
