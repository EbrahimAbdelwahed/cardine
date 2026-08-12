"""Bounded, in-memory diagnostics for validated tutor decisions.

Only the opaque correlation id and the typed decision discriminator are kept.
Learner/model text, prompts, arguments, sources, timing, lifecycle phases, and
HTTP payloads never enter this store.
"""

from __future__ import annotations

import contextvars
import hashlib
from collections import OrderedDict
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue

MAX_TURN_TRACES = 24


@dataclass(slots=True)
class _Trace:
    trace_id: str
    decision: JsonObject | None = None


_CURRENT: contextvars.ContextVar[tuple[TurnTraceStore, str] | None] = (
    contextvars.ContextVar("cardine_turn_trace", default=None)
)


class TurnTraceStore:
    """Own a process-local ring of correlated validated tutor decisions."""

    def __init__(
        self,
        *,
        max_traces: int = MAX_TURN_TRACES,
        # Kept as a compatibility argument for callers that configured the
        # previous event tracer; decisions are inherently one record per turn.
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
        token = _CURRENT.set((self, trace_id))
        try:
            yield trace_id
        finally:
            _CURRENT.reset(token)

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
        with self._lock:
            trace = self._traces.get(trace_id)
            if trace is not None:
                trace.decision = payload
                self._traces.move_to_end(trace_id)

    def snapshot(self) -> JsonObject:
        with self._lock:
            traces: tuple[JsonObject, ...] = tuple(
                cast(
                    JsonObject,
                    {
                        "trace_id": trace.trace_id,
                        "decision": cast(JsonObject, dict(trace.decision)),
                    },
                )
                for trace in self._traces.values()
                if trace.decision is not None
            )
            latest = next(
                (trace.trace_id for trace in reversed(self._traces.values()) if trace.decision),
                None,
            )
        return {
            "schema_version": 3,
            "latest_trace_id": latest,
            "turn_traces": traces,
            "retention": {
                "max_traces": self._max_traces,
                "storage": "memory_only",
                "external_telemetry": False,
                "payload_capture": False,
            },
        }


def record_turn_decision(decision: object) -> None:
    current = _CURRENT.get()
    if current is not None:
        current[0].record_decision(current[1], decision)


def current_turn_trace_id() -> str | None:
    current = _CURRENT.get()
    return None if current is None else current[1]
