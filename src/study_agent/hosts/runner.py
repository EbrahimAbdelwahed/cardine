"""Bounded, provider-neutral orchestration for an external tutor host.

The runner is deliberately operational: canonical study state remains owned by
the capability gateway and its existing event services.  This module owns only
the decision boundary, host receipts, and an opaque continuation record.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import TYPE_CHECKING

from study_agent.capabilities.contracts import (
    CancelledCapabilityOutcome,
    CapabilityContinuation,
    CapabilityGatewayError,
    CapabilityGatewayErrorCode,
    CompletedCapabilityOutcome,
    FailedCapabilityOutcome,
    StaleCapabilityOutcome,
    SuspendedCapabilityOutcome,
    TerminatedCapabilityOutcome,
    TutorCapabilityId,
)
from study_agent.capabilities.fingerprints import (
    capability_output_fingerprint,
    capability_retry_fingerprint,
)
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    ModelRunId,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.domain._validation import JsonObject, JsonValue, freeze_json, freeze_object
from study_agent.playbooks import ReadDependency, ToolBehaviorPin, VersionPins
from study_agent.ports.tutor_host import (
    RetryableTutorDecisionError,
    TutorDecisionPort,
    TutorInterruptionToken,
)
from study_agent.ports.tutor_runner import (
    TutorCapabilityGatewayPort,
    TutorCompletionHandoffStore,
    TutorContinuationStore,
    TutorHostActionIdentityPort,
    TutorHostAuthorityPort,
)
from study_agent.skills import ArtifactReference, SemanticVersion

from .context import TutorHostContextAssembler
from .contracts import (
    AnswerDialogueDecision,
    AskLearnerDecision,
    AssistantMessageDecision,
    HostActionIdentity,
    HostRetryReceipt,
    InvokeToolDecision,
    PendingContinuationDescriptor,
    StartCapabilityDecision,
    StopDecision,
    TutorCapabilityCompletionReference,
    TutorDecision,
    TutorHostContext,
    TutorPresentationKind,
    TutorPresentationReceipt,
    TutorStopReason,
    decision_fingerprint,
    validate_decision,
)

if TYPE_CHECKING:
    from study_agent.ports.assessment import LearnerEvidenceViewPort
    from study_agent.ports.tutor_snapshot import TutorSnapshotPort


MAX_HOST_RETRY_ATTEMPTS = 1_024
MAX_HOST_TEXT = 4_000


class TutorHostRunStatus(StrEnum):
    COMPLETED = "completed"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"
    CANCELLED = "cancelled"
    FAILED = "failed"
    IN_PROGRESS = "in_progress"
    NEEDS_LEARNER_INPUT = "needs_learner_input"
    ASSISTANT_MESSAGE = "assistant_message"
    STOPPED = "stopped"
    INTERRUPTED = "interrupted"
    BUDGET_EXHAUSTED = "budget_exhausted"


class _DecisionBudgetExhausted(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TutorHostLimits:
    max_decisions: int
    max_provider_attempts_per_decision: int
    max_stale_refreshes: int
    max_emitted_text_chars: int

    def __post_init__(self) -> None:
        for value, name in (
            (self.max_decisions, "max_decisions"),
            (self.max_provider_attempts_per_decision, "max_provider_attempts_per_decision"),
            (self.max_stale_refreshes, "max_stale_refreshes"),
            (self.max_emitted_text_chars, "max_emitted_text_chars"),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")


@dataclass(frozen=True, slots=True)
class TutorHostRunResult:
    status: TutorHostRunStatus
    retry_receipt: HostRetryReceipt | None = None
    learner_text: str | None = None
    completed_output: JsonValue | None = None
    pending_continuation: PendingContinuationDescriptor | None = None
    presentation_receipt: TutorPresentationReceipt | None = None
    completion_reference: TutorCapabilityCompletionReference | None = None
    observed_host_context_sequence: int | None = None
    failure_reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.status, TutorHostRunStatus):
            raise TypeError("host result status must use TutorHostRunStatus")
        if self.retry_receipt is not None and not isinstance(
            self.retry_receipt, HostRetryReceipt
        ):
            raise TypeError("host retry receipt is invalid")
        if self.learner_text is not None:
            _require_text(self.learner_text, "learner_text", MAX_HOST_TEXT)
        if self.completed_output is not None:
            object.__setattr__(self, "completed_output", freeze_json(self.completed_output))
        if self.pending_continuation is not None and not isinstance(
            self.pending_continuation, PendingContinuationDescriptor
        ):
            raise TypeError("pending continuation descriptor is invalid")
        if self.presentation_receipt is not None and not isinstance(
            self.presentation_receipt, TutorPresentationReceipt
        ):
            raise TypeError("presentation receipt is invalid")
        if self.completion_reference is not None and not isinstance(
            self.completion_reference, TutorCapabilityCompletionReference
        ):
            raise TypeError("completion reference is invalid")
        if self.observed_host_context_sequence is not None and (
            type(self.observed_host_context_sequence) is not int
            or self.observed_host_context_sequence < 0
        ):
            raise ValueError("observed_host_context_sequence must be non-negative")
        if self.failure_reason is not None and (
            self.status
            not in {
                TutorHostRunStatus.FAILED,
                TutorHostRunStatus.BUDGET_EXHAUSTED,
            }
            or self.failure_reason
            not in {
                "authentication",
                "model_unavailable",
                "endpoint_incompatible",
                "rate_limited",
                "timeout",
                "protocol_error",
                "unavailable",
            }
        ):
            raise ValueError("host failure reason is invalid")

        if self.status is TutorHostRunStatus.COMPLETED:
            if (
                (self.retry_receipt is not None and self.completion_reference is None)
                or self.learner_text is not None
                or self.pending_continuation is not None
                or self.presentation_receipt is not None
            ):
                raise ValueError("completed result may expose output only")
            if self.completion_reference is not None and self.retry_receipt is None:
                raise ValueError("completion reference requires a retry receipt")
        elif self.status is TutorHostRunStatus.SUSPENDED:
            if (
                self.pending_continuation is None
                or self.learner_text is not None
                or self.completed_output is not None
                or self.completion_reference is not None
            ):
                raise ValueError("suspended result requires pending continuation only")
        elif self.status in {
            TutorHostRunStatus.NEEDS_LEARNER_INPUT,
            TutorHostRunStatus.ASSISTANT_MESSAGE,
        }:
            if (
                self.learner_text is None
                or self.completed_output is not None
                or self.pending_continuation is not None
                or self.completion_reference is not None
            ):
                raise ValueError("question/message result requires bounded text only")
        elif self.status is TutorHostRunStatus.INTERRUPTED:
            if (
                self.learner_text is not None
                or self.completed_output is not None
                or self.presentation_receipt is not None
                or self.completion_reference is not None
            ):
                raise ValueError("interrupted result cannot expose text or output")
        elif (
            self.learner_text is not None
            or self.completed_output is not None
            or self.pending_continuation is not None
            or self.presentation_receipt is not None
            or self.completion_reference is not None
        ):
            raise ValueError("closed result may expose a retry receipt only")

    @property
    def text(self) -> str | None:
        return self.learner_text

    @property
    def output(self) -> JsonValue | None:
        return self.completed_output

    @property
    def pending(self) -> PendingContinuationDescriptor | None:
        return self.pending_continuation

@dataclass(frozen=True, slots=True)
class TutorContinuationRecord:
    """The exact host-only material required to resume a suspended action."""

    continuation: CapabilityContinuation
    execution_context: ExecutionContext
    descriptor: PendingContinuationDescriptor

    def __post_init__(self) -> None:
        if not isinstance(self.continuation, CapabilityContinuation):
            raise TypeError("continuation record continuation is invalid")
        if not isinstance(self.execution_context, ExecutionContext):
            raise TypeError("continuation record execution context is invalid")
        if not isinstance(self.descriptor, PendingContinuationDescriptor):
            raise TypeError("continuation record descriptor is invalid")
        if self.descriptor.fingerprint != self.continuation.fingerprint:
            raise ValueError("continuation descriptor does not bind exact continuation")
        expected_identity = (
            f"{self.continuation.capability_id.value}@"
            f"{self.continuation.capability_version.major}"
        )
        if self.descriptor.capability_identity != expected_identity:
            raise ValueError("continuation descriptor capability identity differs")
        if self.execution_context.session_id is None:
            raise ValueError("continuation record requires a session authority")

    def to_bytes(self) -> bytes:
        """Encode one strict operational record at the continuation boundary."""

        payload: JsonObject = {
            "schema_version": 1,
            "continuation": self.continuation.to_json(),
            "execution_context": {
                "principal_kind": self.execution_context.principal_kind.value,
                "principal_id": self.execution_context.principal_id,
                "course_id": str(self.execution_context.course_id),
                "correlation_id": str(self.execution_context.correlation_id),
                "requested_capabilities": tuple(
                    sorted(self.execution_context.requested_capabilities)
                ),
                "session_id": str(self.execution_context.session_id),
                "model_run_id": (
                    None
                    if self.execution_context.model_run_id is None
                    else str(self.execution_context.model_run_id)
                ),
                "idempotency_key": self.execution_context.idempotency_key,
            },
            "descriptor": self.descriptor.to_json(),
        }
        return _canonical_bytes(payload)

    @classmethod
    def from_bytes(cls, data: bytes) -> TutorContinuationRecord:
        raw = _canonical_object(data, "tutor continuation record")
        _exact(
            raw,
            {"schema_version", "continuation", "execution_context", "descriptor"},
            "tutor continuation record",
        )
        if _integer(raw, "schema_version") != 1:
            raise ValueError("unsupported tutor continuation record schema version")
        continuation = _continuation_from_json(
            _object(raw["continuation"], "continuation")
        )
        context_raw = _object(raw["execution_context"], "execution_context")
        _exact(
            context_raw,
            {
                "principal_kind",
                "principal_id",
                "course_id",
                "correlation_id",
                "requested_capabilities",
                "session_id",
                "model_run_id",
                "idempotency_key",
            },
            "execution_context",
        )
        capabilities = _array(
            context_raw["requested_capabilities"], "requested_capabilities"
        )
        if not all(isinstance(item, str) for item in capabilities):
            raise ValueError("requested capabilities must be strings")
        capability_names = tuple(item for item in capabilities if isinstance(item, str))
        if capability_names != tuple(sorted(capability_names)):
            raise ValueError("requested capabilities must be canonically ordered")
        if len(set(capability_names)) != len(capability_names):
            raise ValueError("requested capabilities must be unique")
        model_run = context_raw["model_run_id"]
        if model_run is not None and not isinstance(model_run, str):
            raise ValueError("model_run_id must be a string or null")
        idempotency = context_raw["idempotency_key"]
        if idempotency is not None and not isinstance(idempotency, str):
            raise ValueError("idempotency_key must be a string or null")
        context = ExecutionContext(
            PrincipalKind(_string(context_raw, "principal_kind")),
            _string(context_raw, "principal_id"),
            CourseId(_string(context_raw, "course_id")),
            CorrelationId(_string(context_raw, "correlation_id")),
            frozenset(capability_names),
            SessionId(_string(context_raw, "session_id")),
            None if model_run is None else ModelRunId(model_run),
            idempotency,
        )
        descriptor = _descriptor_from_json(_object(raw["descriptor"], "descriptor"))
        record = cls(continuation, context, descriptor)
        if record.to_bytes() != data:
            raise ValueError("tutor continuation record is not semantically canonical")
        return record


@dataclass(frozen=True, slots=True)
class ScriptedDecision:
    context_fingerprint: str
    decision: TutorDecision | BaseException

    def __post_init__(self) -> None:
        _require_sha256(self.context_fingerprint, "scripted context fingerprint")


class ScriptedDecisionError(ValueError):
    """Fail-closed mismatch/exhaustion in the deterministic decision adapter."""


class ScriptedTutorDecisionPort:
    """Deterministic decision adapter used by offline hosts and contract tests."""

    def __init__(
        self,
        entries: Sequence[ScriptedDecision | tuple[str, TutorDecision | BaseException]],
    ) -> None:
        normalized: list[ScriptedDecision] = []
        for entry in entries:
            item = entry if isinstance(entry, ScriptedDecision) else ScriptedDecision(*entry)
            normalized.append(item)
        self._entries = tuple(normalized)
        self._index = 0

    async def decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        if interruption.is_interrupted():
            raise ScriptedDecisionError("scripted decision interrupted")
        if self._index >= len(self._entries):
            raise ScriptedDecisionError("scripted decisions exhausted")
        expected = self._entries[self._index]
        if expected.context_fingerprint != context.fingerprint:
            raise ScriptedDecisionError("scripted context fingerprint mismatch")
        self._index += 1
        if interruption.is_interrupted():
            raise ScriptedDecisionError("scripted decision interrupted")
        if isinstance(expected.decision, BaseException):
            raise expected.decision
        return expected.decision


class TutorCompletionHandoffState(StrEnum):
    """Durable lifecycle of one host capability handoff."""

    ISSUED = "issued"
    COMPLETED = "completed"
    STALE = "stale"


@dataclass(frozen=True, slots=True)
class TutorCompletionHandoff:
    """Bounded operational record persisted around the gateway call.

    The record contains the exact action and trusted execution context needed
    to replay an issued call after process loss.  It never stores provider
    output; a completed record carries only the closed completion reference.
    """

    state: TutorCompletionHandoffState
    course_id: CourseId
    session_id: SessionId
    host_turn_id: str
    generation: int
    observed_host_context_sequence: int
    context_fingerprint: str
    retry_receipt: HostRetryReceipt
    capability_id: str
    capability_identity: str
    manifest_fingerprint: str
    action: JsonObject
    execution_context: ExecutionContext
    completion_reference: TutorCapabilityCompletionReference | None = None
    record_fingerprint: str | None = None

    SCHEMA_VERSION = 1

    def __post_init__(self) -> None:
        if not isinstance(self.state, TutorCompletionHandoffState):
            raise TypeError("completion handoff state is invalid")
        if not isinstance(self.course_id, CourseId) or not isinstance(
            self.session_id, SessionId
        ):
            raise TypeError("completion handoff scope is invalid")
        _require_opaque(self.host_turn_id, "handoff host_turn_id")
        if type(self.generation) is not int or self.generation < 1:
            raise ValueError("handoff generation must be positive")
        if (
            type(self.observed_host_context_sequence) is not int
            or self.observed_host_context_sequence < 0
        ):
            raise ValueError("handoff observed sequence must be non-negative")
        _require_sha256(self.context_fingerprint, "handoff context_fingerprint")
        if not isinstance(self.retry_receipt, HostRetryReceipt):
            raise TypeError("handoff retry receipt is invalid")
        if self.retry_receipt.host_turn_id != self.host_turn_id:
            raise ValueError("handoff retry receipt does not bind host turn")
        _require_text(self.capability_id, "handoff capability id", 128)
        _require_text(self.capability_identity, "handoff capability identity", 128)
        if not self.capability_identity.startswith(f"{self.capability_id}@"):
            raise ValueError("handoff capability identity differs from id")
        _require_sha256(self.manifest_fingerprint, "handoff manifest_fingerprint")
        action = freeze_object(self.action)
        _validate_handoff_action(action, self.capability_id)
        object.__setattr__(self, "action", action)
        if not isinstance(self.execution_context, ExecutionContext):
            raise TypeError("handoff execution context is invalid")
        if (
            self.execution_context.course_id != self.course_id
            or self.execution_context.session_id != self.session_id
        ):
            raise ValueError("handoff execution context scope differs")
        if self.state is TutorCompletionHandoffState.COMPLETED:
            if self.completion_reference is None:
                raise ValueError("completed handoff requires a completion reference")
            if (
                self.completion_reference.capability_identity != self.capability_identity
                or self.completion_reference.manifest_fingerprint != self.manifest_fingerprint
            ):
                raise ValueError("completion reference does not bind handoff capability")
        elif self.completion_reference is not None:
            raise ValueError("only completed handoffs carry a completion reference")
        if self.record_fingerprint is not None:
            _require_sha256(self.record_fingerprint, "handoff record_fingerprint")
            if self.record_fingerprint != self._computed_record_fingerprint():
                raise ValueError("completion handoff integrity fingerprint differs")

    @property
    def key(self) -> str:
        return completion_handoff_key(self.course_id, self.session_id, self.host_turn_id)

    @property
    def gateway_retry_fingerprint(self) -> str:
        key = self.execution_context.idempotency_key
        if key is None:
            raise ValueError("handoff execution context has no idempotency key")
        return capability_retry_fingerprint(key)

    def decision(self, context: TutorHostContext) -> TutorDecision:
        """Rehydrate and validate the exact action against fresh redacted context."""

        action = self.action
        if action["kind"] == "start":
            decision: TutorDecision = StartCapabilityDecision(
                _string(action, "capability_id"), _object(action["inputs"], "inputs")
            )
        else:
            decision = AnswerDialogueDecision(
                _string(action, "continuation_fingerprint"), action["response"]
            )
        validate_decision(decision, context)
        if decision_fingerprint(decision) != self.retry_receipt.action_fingerprint:
            raise ValueError("handoff decision fingerprint differs")
        return decision

    def to_json(self) -> JsonObject:
        payload = {
            "schema_version": self.SCHEMA_VERSION,
            "state": self.state.value,
            "course_id": str(self.course_id),
            "session_id": str(self.session_id),
            "host_turn_id": self.host_turn_id,
            "generation": self.generation,
            "observed_host_context_sequence": self.observed_host_context_sequence,
            "context_fingerprint": self.context_fingerprint,
            "retry_receipt": self.retry_receipt.to_json(),
            "capability_id": self.capability_id,
            "capability_identity": self.capability_identity,
            "manifest_fingerprint": self.manifest_fingerprint,
            "action": self.action,
            "execution_context": _execution_context_to_json(self.execution_context),
            "completion_reference": (
                None
                if self.completion_reference is None
                else self.completion_reference.to_json()
            ),
        }
        payload["record_fingerprint"] = self._computed_record_fingerprint()
        return payload

    def _payload_json(self) -> JsonObject:
        return {
            "schema_version": self.SCHEMA_VERSION,
            "state": self.state.value,
            "course_id": str(self.course_id),
            "session_id": str(self.session_id),
            "host_turn_id": self.host_turn_id,
            "generation": self.generation,
            "observed_host_context_sequence": self.observed_host_context_sequence,
            "context_fingerprint": self.context_fingerprint,
            "retry_receipt": self.retry_receipt.to_json(),
            "capability_id": self.capability_id,
            "capability_identity": self.capability_identity,
            "manifest_fingerprint": self.manifest_fingerprint,
            "action": self.action,
            "execution_context": _execution_context_to_json(self.execution_context),
            "completion_reference": (
                None
                if self.completion_reference is None
                else self.completion_reference.to_json()
            ),
        }

    def _computed_record_fingerprint(self) -> str:
        return sha256(
            b"study-agent-tutor-completion-handoff-integrity-v1\0"
            + _canonical_bytes(self._payload_json())
        ).hexdigest()

    def to_bytes(self) -> bytes:
        return _canonical_bytes(self.to_json())

    @classmethod
    def from_bytes(cls, data: bytes) -> TutorCompletionHandoff:
        raw = _canonical_object(data, "tutor completion handoff")
        _exact(
            raw,
            {
                "schema_version",
                "state",
                "course_id",
                "session_id",
                "host_turn_id",
                "generation",
                "observed_host_context_sequence",
                "context_fingerprint",
                "retry_receipt",
                "capability_id",
                "capability_identity",
                "manifest_fingerprint",
                "action",
                "execution_context",
                "completion_reference",
                "record_fingerprint",
            },
            "tutor completion handoff",
        )
        if _integer(raw, "schema_version") != cls.SCHEMA_VERSION:
            raise ValueError("unsupported tutor completion handoff schema version")
        receipt = HostRetryReceipt.from_bytes(
            _canonical_bytes(_object(raw["retry_receipt"], "retry_receipt"))
        )
        context = _execution_context_from_json(
            _object(raw["execution_context"], "execution_context")
        )
        reference_raw = raw["completion_reference"]
        reference = (
            None
            if reference_raw is None
            else _completion_reference_from_json(_object(reference_raw, "completion_reference"))
        )
        record = cls(
            TutorCompletionHandoffState(_string(raw, "state")),
            CourseId(_string(raw, "course_id")),
            SessionId(_string(raw, "session_id")),
            _string(raw, "host_turn_id"),
            _integer(raw, "generation"),
            _integer(raw, "observed_host_context_sequence"),
            _string(raw, "context_fingerprint"),
            receipt,
            _string(raw, "capability_id"),
            _string(raw, "capability_identity"),
            _string(raw, "manifest_fingerprint"),
            _object(raw["action"], "action"),
            context,
            reference,
            _string(raw, "record_fingerprint"),
        )
        if record.to_bytes() != data:
            raise ValueError("tutor completion handoff is not semantically canonical")
        return record


def completion_handoff_key(
    course_id: CourseId, session_id: SessionId, host_turn_id: str
) -> str:
    """Derive the collision-resistant namespaced slot for one host turn."""

    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("completion handoff key requires typed scope")
    _require_opaque(host_turn_id, "host_turn_id")
    digest = sha256(
        b"study-agent-tutor-completion-handoff-v1\0"
        + str(course_id).encode("utf-8")
        + b"\0"
        + str(session_id).encode("utf-8")
        + b"\0"
        + host_turn_id.encode("utf-8")
    ).hexdigest()
    return f"tutor-completion-handoff-sha256:{digest}"


def _validate_handoff_action(action: JsonObject, capability_id: str) -> None:
    kind = action.get("kind")
    if kind == "start":
        _exact(action, {"kind", "capability_id", "inputs"}, "start handoff action")
        if _string(action, "capability_id") != capability_id:
            raise ValueError("handoff action capability differs")
        _object(action["inputs"], "inputs")
    elif kind == "resume":
        _exact(
            action,
            {"kind", "continuation_fingerprint", "response"},
            "resume handoff action",
        )
        _require_sha256(
            _string(action, "continuation_fingerprint"),
            "handoff continuation fingerprint",
        )
    else:
        raise ValueError("handoff action kind is invalid")


def _execution_context_to_json(context: ExecutionContext) -> JsonObject:
    return {
        "principal_kind": context.principal_kind.value,
        "principal_id": context.principal_id,
        "course_id": str(context.course_id),
        "correlation_id": str(context.correlation_id),
        "requested_capabilities": tuple(sorted(context.requested_capabilities)),
        "session_id": None if context.session_id is None else str(context.session_id),
        "model_run_id": None if context.model_run_id is None else str(context.model_run_id),
        "idempotency_key": context.idempotency_key,
    }


def _execution_context_from_json(raw: JsonObject) -> ExecutionContext:
    _exact(
        raw,
        {
            "principal_kind",
            "principal_id",
            "course_id",
            "correlation_id",
            "requested_capabilities",
            "session_id",
            "model_run_id",
            "idempotency_key",
        },
        "execution_context",
    )
    capabilities = _array(raw["requested_capabilities"], "requested_capabilities")
    if not all(isinstance(item, str) for item in capabilities):
        raise ValueError("requested capabilities must be strings")
    session = raw["session_id"]
    model_run = raw["model_run_id"]
    idempotency = raw["idempotency_key"]
    if session is not None and not isinstance(session, str):
        raise ValueError("session_id must be a string or null")
    if model_run is not None and not isinstance(model_run, str):
        raise ValueError("model_run_id must be a string or null")
    if idempotency is not None and not isinstance(idempotency, str):
        raise ValueError("idempotency_key must be a string or null")
    return ExecutionContext(
        PrincipalKind(_string(raw, "principal_kind")),
        _string(raw, "principal_id"),
        CourseId(_string(raw, "course_id")),
        CorrelationId(_string(raw, "correlation_id")),
        frozenset(item for item in capabilities if isinstance(item, str)),
        None if session is None else SessionId(session),
        None if model_run is None else ModelRunId(model_run),
        idempotency,
    )


def _completion_reference_from_json(
    raw: JsonObject,
) -> TutorCapabilityCompletionReference:
    _exact(
        raw,
        {
            "capability_identity",
            "manifest_fingerprint",
            "run_id",
            "output_fingerprint",
            "retry_receipt_fingerprint",
        },
        "completion reference",
    )
    return TutorCapabilityCompletionReference(
        _string(raw, "capability_identity"),
        _string(raw, "manifest_fingerprint"),
        RunId(_string(raw, "run_id")),
        _string(raw, "output_fingerprint"),
        _string(raw, "retry_receipt_fingerprint"),
    )


def _handoff_action(decision: TutorDecision) -> JsonObject:
    if isinstance(decision, StartCapabilityDecision):
        return {
            "kind": "start",
            "capability_id": decision.capability_id,
            "inputs": decision.inputs,
        }
    if isinstance(decision, AnswerDialogueDecision):
        return {
            "kind": "resume",
            "continuation_fingerprint": decision.continuation_fingerprint,
            "response": decision.response,
        }
    raise TypeError("only capability actions can be handed off")


class _MemoryCompletionHandoffStore:
    """Small default store for direct host tests without repository composition."""

    def __init__(self) -> None:
        self._values: dict[str, bytes] = {}

    def create(self, key: str, payload: bytes) -> bool:
        if key in self._values:
            return False
        self._values[key] = bytes(payload)
        return True

    def load(self, key: str) -> bytes:
        try:
            return self._values[key]
        except KeyError:
            raise KeyError(key) from None

    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool:
        if self._values.get(key) != expected:
            return False
        self._values[key] = bytes(replacement)
        return True


class TutorHostRunner:
    """Execute one bounded host turn over the existing capability gateway."""

    def __init__(
        self,
        decision_port: TutorDecisionPort,
        snapshots: TutorSnapshotPort | None,
        evidence: LearnerEvidenceViewPort | None,
        gateway: TutorCapabilityGatewayPort,
        authority: TutorHostAuthorityPort,
        action_identity: TutorHostActionIdentityPort,
        continuation_store: TutorContinuationStore,
        limits: TutorHostLimits,
        *,
        context_assembler: TutorHostContextAssembler | None = None,
        completion_handoff_store: TutorCompletionHandoffStore | None = None,
        tool_gateway: object | None = None,
    ) -> None:
        if context_assembler is None:
            if snapshots is None or evidence is None:
                raise TypeError("snapshots and evidence are required without a context assembler")
            context_assembler = TutorHostContextAssembler(snapshots, evidence, gateway)
        self._decision_port = decision_port
        self._gateway = gateway
        self._authority = authority
        self._action_identity = action_identity
        self._store = continuation_store
        self._limits = limits
        self._assembler = context_assembler
        self._handoffs = completion_handoff_store or _MemoryCompletionHandoffStore()
        self._tool_gateway = tool_gateway

    @property
    def continuation_store(self) -> TutorContinuationStore:
        """Exact operational store bound to this runner composition."""

        return self._store

    @property
    def completion_handoff_store(self) -> TutorCompletionHandoffStore:
        """Exact durable handoff store bound to this runner composition."""

        return self._handoffs

    async def verify_model_readiness(
        self, course_id: CourseId, session_id: SessionId
    ) -> None:
        """Exercise the exact decision schema without writing a learner turn.

        Settings uses this probe instead of a shallow provider ping: it builds
        the current source-backed tutor context and asks the same decision port
        used by a real chat turn, but deliberately does not execute its result.
        """

        if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
            raise TypeError("model readiness requires typed course and session ids")
        context = self._assembler.assemble(course_id, session_id)
        await self._decision_port.decide(context, _NeverInterrupted())

    async def run(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        interruption: TutorInterruptionToken,
        *,
        retry_receipt: HostRetryReceipt | None = None,
        pending_fingerprint: str | None = None,
    ) -> TutorHostRunResult:
        if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
            raise TypeError("host runner requires typed course and session ids")
        _require_opaque(host_turn_id, "host_turn_id")
        if retry_receipt is not None and not isinstance(retry_receipt, HostRetryReceipt):
            raise TypeError("retry_receipt is invalid")
        if pending_fingerprint is not None:
            _require_sha256(pending_fingerprint, "pending_fingerprint")
        if _interrupted(interruption):
            return _interrupted_result()

        try:
            handoff = self._load_handoff(course_id, session_id, host_turn_id)
        except (OSError, RuntimeError, TypeError, ValueError):
            # A tampered or corrupt handoff is a closed failure.  In
            # particular, do not fall through to model decision work.
            return _failed()
        if handoff is not None and handoff.state is TutorCompletionHandoffState.COMPLETED:
            return self._result_from_handoff(handoff)
        if handoff is not None and handoff.state is TutorCompletionHandoffState.STALE:
            handoff = None

        selected: TutorContinuationRecord | None = None
        if pending_fingerprint is not None:
            try:
                selected = self._load(course_id, session_id, pending_fingerprint, interruption)
            except Exception:
                return _interrupted_result() if _interrupted(interruption) else _failed()
            if selected is None:
                return _interrupted_result() if _interrupted(interruption) else _failed()

        decisions = 0
        stale_refreshes = 0
        generation = 1
        while True:
            if _interrupted(interruption):
                return _interrupted_result(selected, retry_receipt)
            if decisions >= self._limits.max_decisions:
                return _budget()
            pending = None if selected is None else selected.descriptor
            try:
                context = self._assemble(
                    course_id, session_id, pending, interruption
                )
            except Exception:
                return (
                    _interrupted_result(selected, retry_receipt)
                    if _interrupted(interruption)
                    else _failed()
                )
            if context is None:
                return _interrupted_result(selected, retry_receipt)

            retry_action: HostRetryReceipt | None = None
            if handoff is not None:
                if context.fingerprint != handoff.context_fingerprint:
                    return _failed(handoff.retry_receipt)
                try:
                    decision = handoff.decision(context)
                except (TypeError, ValueError):
                    return _failed(handoff.retry_receipt)
                retry_action = HostRetryReceipt(
                    handoff.retry_receipt.host_turn_id,
                    handoff.retry_receipt.action_identity_fingerprint,
                    handoff.retry_receipt.context_fingerprint,
                    handoff.retry_receipt.action_fingerprint,
                    handoff.retry_receipt.decision_generation,
                    handoff.retry_receipt.attempt + 1,
                )
                updated = replace(
                    handoff, retry_receipt=retry_action, record_fingerprint=None
                )
                if not self._replace_handoff(handoff, updated, interruption):
                    return _failed(retry_action)
                handoff = updated
                trusted_context = handoff.execution_context
                decisions += 1
            else:
                try:
                    decision = await self._decide(context, interruption)
                except RetryableTutorDecisionError as error:
                    return _budget(getattr(error, "failure_reason", None))
                except _DecisionBudgetExhausted:
                    return _budget()
                except Exception as error:
                    return (
                        _interrupted_result(selected, retry_receipt)
                        if _interrupted(interruption)
                        else _failed(
                            failure_reason=getattr(error, "failure_reason", None)
                        )
                    )
                if _interrupted(interruption):
                    return _interrupted_result(selected, retry_receipt)
                decisions += 1
            try:
                validate_decision(decision, context)
            except (TypeError, ValueError):
                return _failed()
            if selected is not None and not isinstance(
                decision, AnswerDialogueDecision
            ):
                # A pending continuation can only be resolved through its
                # advertised response schema.  Presentation/stop decisions
                # must not silently abandon operational capability state.
                return _failed()

            if isinstance(decision, (AssistantMessageDecision,)):
                if len(decision.message) > self._limits.max_emitted_text_chars:
                    return _budget()
                return TutorHostRunResult(
                    TutorHostRunStatus.ASSISTANT_MESSAGE,
                    learner_text=decision.message,
                    presentation_receipt=self._presentation_receipt(
                        host_turn_id,
                        context,
                        TutorPresentationKind.ASSISTANT_MESSAGE,
                        decision.message,
                        decision,
                    ),
                )
            if isinstance(decision, AskLearnerDecision):
                if len(decision.question) > self._limits.max_emitted_text_chars:
                    return _budget()
                return TutorHostRunResult(
                    TutorHostRunStatus.NEEDS_LEARNER_INPUT,
                    learner_text=decision.question,
                    presentation_receipt=self._presentation_receipt(
                        host_turn_id,
                        context,
                        TutorPresentationKind.LEARNER_QUESTION,
                        decision.question,
                        decision,
                    ),
                )
            if isinstance(decision, StopDecision):
                status = (
                    TutorHostRunStatus.COMPLETED
                    if decision.reason is TutorStopReason.COMPLETED
                    else TutorHostRunStatus.STOPPED
                )
                return TutorHostRunResult(status)

            if isinstance(decision, InvokeToolDecision):
                gateway = getattr(self, "_tool_gateway", None)
                if gateway is None:
                    return _failed()
                try:
                    result = await gateway.invoke(
                        decision.tool_name,
                        decision.arguments,
                        course_id,
                        session_id,
                        host_turn_id,
                    )
                except Exception:
                    return _failed()
                if getattr(result, "error", None) is not None:
                    return _failed()
                receipt = self._presentation_receipt(
                    host_turn_id,
                    context,
                    TutorPresentationKind.ASSISTANT_MESSAGE,
                    "Operazione di studio registrata nel repository.",
                    decision,
                )
                value = getattr(result, "value", None)
                observed = value.get("high_water_sequence") if isinstance(value, Mapping) else None
                if type(observed) is int and observed >= receipt.observed_host_context_sequence:
                    receipt = replace(receipt, observed_host_context_sequence=observed)
                return TutorHostRunResult(
                    TutorHostRunStatus.ASSISTANT_MESSAGE,
                    learner_text="Operazione di studio registrata nel repository.",
                    presentation_receipt=receipt,
                )

            retry_action = None
            capability_identity: str | None = None
            capability_manifest_fingerprint: str | None = None
            if isinstance(decision, StartCapabilityDecision):
                try:
                    capability_id = TutorCapabilityId(decision.capability_id)
                except ValueError:
                    return _failed()
                capability = next(
                    (
                        item
                        for item in context.advertised_capabilities
                        if item.id == decision.capability_id
                    ),
                    None,
                )
                if capability is None:
                    return _failed()
                capability_identity = capability.identity
                capability_manifest_fingerprint = capability.manifest_fingerprint
                if handoff is not None:
                    if handoff.capability_identity != capability_identity:
                        return _failed(handoff.retry_receipt)
                    retry_action = handoff.retry_receipt
                    action_identity = HostActionIdentity(
                        handoff.execution_context.idempotency_key or "handoff-action"
                    )
                else:
                    action_result = self._issue_action(
                        host_turn_id, context, decision, generation, retry_receipt, interruption
                    )
                    if action_result is None:
                        return (
                            _interrupted_result(selected, retry_receipt)
                            if _interrupted(interruption)
                            else _failed(retry_receipt)
                        )
                    action_identity, retry_action = action_result
                    try:
                        trusted_context = self._authority.create_context(
                            course_id, session_id, capability_id, action_identity
                        )
                    except Exception:
                        return (
                            _interrupted_result(selected, retry_action)
                            if _interrupted(interruption)
                            else _failed(retry_action)
                        )
                    if (
                        not isinstance(trusted_context, ExecutionContext)
                        or trusted_context.course_id != course_id
                        or trusted_context.session_id != session_id
                    ):
                        return _failed(retry_action)
                    if not self._issue_handoff(
                        course_id,
                        session_id,
                        host_turn_id,
                        context,
                        decision,
                        action_identity,
                        retry_action,
                        capability_identity,
                        capability_manifest_fingerprint,
                        trusted_context,
                        generation,
                        interruption,
                    ):
                        return (
                            _interrupted_result(selected, retry_action)
                            if _interrupted(interruption)
                            else _failed(retry_action)
                        )
                if _interrupted(interruption):
                    return _interrupted_result(selected, retry_action)
                try:
                    outcome = await self._gateway.start(
                        capability_id, decision.inputs, trusted_context
                    )
                except CapabilityGatewayError as error:
                    if _interrupted(interruption):
                        return _interrupted_result(selected, retry_action)
                    if error.code is CapabilityGatewayErrorCode.IN_PROGRESS:
                        return TutorHostRunResult(
                            TutorHostRunStatus.IN_PROGRESS, retry_action
                        )
                    return _failed(retry_action)
                except Exception:
                    return (
                        _interrupted_result(selected, retry_action)
                        if _interrupted(interruption)
                        else _failed(retry_action)
                    )
            elif isinstance(decision, AnswerDialogueDecision):
                if selected is None:
                    return _failed()
                trusted_context = selected.execution_context
                capability_identity = selected.descriptor.capability_identity
                capability_manifest_fingerprint = next(
                    (
                        item.manifest_fingerprint
                        for item in context.advertised_capabilities
                        if item.identity == capability_identity
                    ),
                    None,
                )
                if handoff is not None:
                    if handoff.action.get("kind") != "resume":
                        return _failed(handoff.retry_receipt)
                    retry_action = handoff.retry_receipt
                    action_identity = HostActionIdentity(
                        handoff.execution_context.idempotency_key or "handoff-action"
                    )
                else:
                    action_result = self._issue_action(
                        host_turn_id, context, decision, generation, retry_receipt, interruption
                    )
                    if action_result is None:
                        return (
                            _interrupted_result(selected, retry_receipt)
                            if _interrupted(interruption)
                            else _failed(retry_receipt)
                        )
                    action_identity, retry_action = action_result
                    if capability_manifest_fingerprint is None:
                        return _failed(retry_action)
                    if not self._issue_handoff(
                        course_id,
                        session_id,
                        host_turn_id,
                        context,
                        decision,
                        action_identity,
                        retry_action,
                        capability_identity,
                        capability_manifest_fingerprint,
                        selected.execution_context,
                        generation,
                        interruption,
                    ):
                        return (
                            _interrupted_result(selected, retry_action)
                            if _interrupted(interruption)
                            else _failed(retry_action)
                        )
                del action_identity
                if _interrupted(interruption):
                    return _interrupted_result(selected, retry_action)
                try:
                    outcome = await self._gateway.resume(
                        selected.continuation,
                        decision.response,
                        selected.execution_context,
                    )
                except CapabilityGatewayError as error:
                    if _interrupted(interruption):
                        return _interrupted_result(selected, retry_action)
                    if error.code is CapabilityGatewayErrorCode.IN_PROGRESS:
                        return TutorHostRunResult(
                            TutorHostRunStatus.IN_PROGRESS, retry_action
                        )
                    return _failed(retry_action)
                except Exception:
                    return _failed(retry_action)
            else:
                return _failed()

            if _interrupted(interruption):
                return _interrupted_result(selected, retry_action)
            if isinstance(outcome, StaleCapabilityOutcome):
                finalized = self._finalize_handoff(
                    course_id,
                    session_id,
                    host_turn_id,
                    TutorCompletionHandoffState.STALE,
                    None,
                    interruption,
                )
                if (
                    finalized is not None
                    and finalized.state is TutorCompletionHandoffState.COMPLETED
                ):
                    return self._result_from_handoff(finalized)
                handoff = None
                if selected is not None:
                    try:
                        self._delete(
                            course_id,
                            session_id,
                            selected.descriptor.fingerprint,
                            interruption,
                        )
                    except Exception:
                        return _failed(retry_action)
                    if _interrupted(interruption):
                        return _interrupted_result(selected, retry_action)
                    selected = None
                stale_refreshes += 1
                if stale_refreshes > self._limits.max_stale_refreshes:
                    return _budget()
                generation += 1
                retry_receipt = None
                continue
            if isinstance(outcome, SuspendedCapabilityOutcome):
                self._finalize_handoff(
                    course_id,
                    session_id,
                    host_turn_id,
                    TutorCompletionHandoffState.STALE,
                    None,
                    interruption,
                )
                descriptor = self._descriptor(context, outcome)
                continuation_context = (
                    trusted_context
                    if isinstance(decision, StartCapabilityDecision)
                    else selected.execution_context
                    if selected is not None
                    else None
                )
                if continuation_context is None:
                    return _failed(retry_action)
                record = TutorContinuationRecord(
                    outcome.continuation,
                    continuation_context,
                    descriptor,
                )
                if not self._create(course_id, session_id, record, interruption):
                    return (
                        _interrupted_result(selected, retry_action)
                        if _interrupted(interruption)
                        else _failed(retry_action)
                    )
                selected = record
                if _interrupted(interruption):
                    return _interrupted_result(selected, retry_action)
                return TutorHostRunResult(
                    TutorHostRunStatus.SUSPENDED,
                    retry_action,
                    pending_continuation=descriptor,
                    presentation_receipt=self._presentation_receipt(
                        host_turn_id,
                        context,
                        TutorPresentationKind.CONTINUATION_REQUEST,
                        descriptor.dialogue_request,
                        decision,
                        continuation_fingerprint=descriptor.fingerprint,
                        capability_identity=descriptor.capability_identity,
                        response_schema=descriptor.response_schema,
                    ),
                )
            completion_reference = self._completion_reference(
                outcome,
                capability_identity,
                capability_manifest_fingerprint,
                trusted_context if isinstance(trusted_context, ExecutionContext) else None,
            )
            finalized = self._finalize_handoff(
                course_id,
                session_id,
                host_turn_id,
                TutorCompletionHandoffState.COMPLETED
                if completion_reference is not None
                else TutorCompletionHandoffState.STALE,
                completion_reference,
                interruption,
            )
            if (
                completion_reference is not None
                and (
                    finalized is None
                    or finalized.state is not TutorCompletionHandoffState.COMPLETED
                )
            ):
                return _failed(retry_action)
            if finalized is not None and finalized.state is TutorCompletionHandoffState.COMPLETED:
                completion_reference = finalized.completion_reference
                retry_action = finalized.retry_receipt
            if selected is not None and isinstance(
                outcome, CompletedCapabilityOutcome
            ):
                try:
                    self._delete(
                        course_id,
                        session_id,
                        selected.descriptor.fingerprint,
                        interruption,
                    )
                except Exception:
                    return _failed(retry_action)
                if _interrupted(interruption):
                    return _interrupted_result(selected, retry_action)
            return self._map_terminal(
                outcome,
                retry_action,
                capability_identity,
                capability_manifest_fingerprint,
                capability_retry_fingerprint(trusted_context.idempotency_key)
                if isinstance(trusted_context, ExecutionContext)
                and trusted_context.idempotency_key is not None
                else None,
                context.tutor_snapshot_sequence,
                completion_reference,
            )

    def _load_handoff(
        self, course_id: CourseId, session_id: SessionId, host_turn_id: str
    ) -> TutorCompletionHandoff | None:
        key = completion_handoff_key(course_id, session_id, host_turn_id)
        try:
            payload = self._handoffs.load(key)
        except KeyError:
            return None
        if not isinstance(payload, bytes):
            raise ValueError("completion handoff payload is not bytes")
        record = TutorCompletionHandoff.from_bytes(payload)
        if (
            record.key != key
            or record.course_id != course_id
            or record.session_id != session_id
            or record.host_turn_id != host_turn_id
        ):
            raise ValueError("completion handoff scope is incompatible")
        return record

    def _issue_handoff(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        context: TutorHostContext,
        decision: TutorDecision,
        action_identity: HostActionIdentity,
        retry_receipt: HostRetryReceipt,
        capability_identity: str,
        manifest_fingerprint: str,
        execution_context: ExecutionContext,
        generation: int,
        interruption: TutorInterruptionToken,
    ) -> bool:
        if _interrupted(interruption):
            return False
        record = TutorCompletionHandoff(
            TutorCompletionHandoffState.ISSUED,
            course_id,
            session_id,
            host_turn_id,
            generation,
            context.tutor_snapshot_sequence,
            context.fingerprint,
            retry_receipt,
            capability_identity.split("@", 1)[0],
            capability_identity,
            manifest_fingerprint,
            _handoff_action(decision),
            execution_context,
        )
        if execution_context.idempotency_key is None:
            return False
        if retry_receipt.action_identity_fingerprint != action_identity.fingerprint:
            return False
        key = record.key
        payload = record.to_bytes()
        try:
            if self._handoffs.create(key, payload):
                return True
            existing_payload = self._handoffs.load(key)
            existing = TutorCompletionHandoff.from_bytes(existing_payload)
        except KeyError:
            return False
        if existing.state is TutorCompletionHandoffState.STALE:
            return self._handoffs.compare_and_set(key, existing_payload, payload)
        # Another runner already owns the issued call.  The losing caller
        # must not invoke the provider; an exact retry will preflight and
        # replay that durable action.
        return False

    def _replace_handoff(
        self,
        expected: TutorCompletionHandoff,
        replacement: TutorCompletionHandoff,
        interruption: TutorInterruptionToken,
    ) -> bool:
        if _interrupted(interruption) or expected.key != replacement.key:
            return False
        try:
            return self._handoffs.compare_and_set(
                expected.key, expected.to_bytes(), replacement.to_bytes()
            )
        except (KeyError, OSError, RuntimeError, TypeError, ValueError):
            return False

    def _finalize_handoff(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        state: TutorCompletionHandoffState,
        completion_reference: TutorCapabilityCompletionReference | None,
        interruption: TutorInterruptionToken,
    ) -> TutorCompletionHandoff | None:
        try:
            current = self._load_handoff(course_id, session_id, host_turn_id)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None
        if current is None:
            return None
        replacement = replace(
            current,
            state=state,
            completion_reference=completion_reference,
            record_fingerprint=None,
        )
        if self._replace_handoff(current, replacement, interruption):
            return replacement
        try:
            return self._load_handoff(course_id, session_id, host_turn_id)
        except (OSError, RuntimeError, TypeError, ValueError):
            return None

    @staticmethod
    def _completion_reference(
        outcome: object,
        capability_identity: str | None,
        manifest_fingerprint: str | None,
        execution_context: ExecutionContext | None,
    ) -> TutorCapabilityCompletionReference | None:
        if not isinstance(outcome, CompletedCapabilityOutcome):
            return None
        if (
            capability_identity is None
            or manifest_fingerprint is None
            or execution_context is None
            or execution_context.idempotency_key is None
        ):
            return None
        return TutorCapabilityCompletionReference(
            capability_identity,
            manifest_fingerprint,
            outcome.run.run_id,
            capability_output_fingerprint(outcome.output),
            capability_retry_fingerprint(execution_context.idempotency_key),
        )

    @staticmethod
    def _result_from_handoff(record: TutorCompletionHandoff) -> TutorHostRunResult:
        if record.completion_reference is None:
            return _failed(record.retry_receipt)
        return TutorHostRunResult(
            TutorHostRunStatus.COMPLETED,
            retry_receipt=record.retry_receipt,
            completion_reference=record.completion_reference,
            observed_host_context_sequence=record.observed_host_context_sequence,
        )

    async def _decide(
        self, context: TutorHostContext, interruption: TutorInterruptionToken
    ) -> TutorDecision:
        attempts = 0
        while True:
            if _interrupted(interruption):
                raise ScriptedDecisionError("decision interrupted")
            attempts += 1
            try:
                return await self._decision_port.decide(context, interruption)
            except RetryableTutorDecisionError:
                if attempts >= self._limits.max_provider_attempts_per_decision:
                    raise _DecisionBudgetExhausted(
                        "decision provider retry budget exhausted"
                    ) from None
                if _interrupted(interruption):
                    raise ScriptedDecisionError("decision interrupted") from None

    def _assemble(
        self,
        course_id: CourseId,
        session_id: SessionId,
        pending: PendingContinuationDescriptor | None,
        interruption: TutorInterruptionToken,
    ) -> TutorHostContext | None:
        if _interrupted(interruption):
            return None
        value = self._assembler.assemble(
            course_id, session_id, pending_continuation=pending
        )
        if _interrupted(interruption):
            return None
        return value

    def _issue_action(
        self,
        host_turn_id: str,
        context: TutorHostContext,
        decision: TutorDecision,
        generation: int,
        receipt: HostRetryReceipt | None,
        interruption: TutorInterruptionToken,
    ) -> tuple[HostActionIdentity, HostRetryReceipt] | None:
        if _interrupted(interruption):
            return None
        action_fingerprint = decision_fingerprint(decision)
        if receipt is not None and (
            receipt.host_turn_id != host_turn_id
            or receipt.context_fingerprint != context.fingerprint
            or receipt.action_fingerprint != action_fingerprint
            or receipt.decision_generation != generation
            or receipt.attempt >= MAX_HOST_RETRY_ATTEMPTS
        ):
            return None
        try:
            identity = self._action_identity.issue(
                host_turn_id, context.fingerprint, action_fingerprint, generation
            )
        except Exception:
            return None
        if _interrupted(interruption):
            return None
        if not isinstance(identity, HostActionIdentity):
            return None
        if receipt is not None:
            if receipt.action_identity_fingerprint != identity.fingerprint:
                return None
            attempt = receipt.attempt + 1
        else:
            attempt = 1
        return identity, HostRetryReceipt(
            host_turn_id,
            identity.fingerprint,
            context.fingerprint,
            action_fingerprint,
            generation,
            attempt,
        )

    def _descriptor(
        self, context: TutorHostContext, outcome: SuspendedCapabilityOutcome
    ) -> PendingContinuationDescriptor:
        identity = next(
            item.identity
            for item in context.advertised_capabilities
            if item.id == outcome.continuation.capability_id.value
        )
        return PendingContinuationDescriptor(
            outcome.continuation.fingerprint,
            identity,
            outcome.continuation.dialogue_step_id,
            outcome.dialogue_request,
            outcome.response_schema,
        )

    @staticmethod
    def _presentation_receipt(
        host_turn_id: str,
        context: TutorHostContext,
        kind: TutorPresentationKind,
        content: str,
        decision: TutorDecision,
        *,
        continuation_fingerprint: str | None = None,
        capability_identity: str | None = None,
        response_schema: JsonObject | None = None,
    ) -> TutorPresentationReceipt:
        return TutorPresentationReceipt(
            host_turn_id=host_turn_id,
            kind=kind,
            content=content,
            observed_host_context_sequence=context.tutor_snapshot_sequence,
            host_context_fingerprint=context.fingerprint,
            decision_fingerprint=decision_fingerprint(decision),
            continuation_fingerprint=continuation_fingerprint,
            capability_identity=capability_identity,
            response_schema=response_schema,
        )

    def _create(
        self,
        course_id: CourseId,
        session_id: SessionId,
        record: TutorContinuationRecord,
        interruption: TutorInterruptionToken,
    ) -> bool:
        if _interrupted(interruption):
            return False
        payload = record.to_bytes()
        try:
            created = self._store.create(
                course_id, session_id, record.descriptor.fingerprint, payload
            )
        except Exception:
            return False
        if _interrupted(interruption):
            return False
        return created or self._same_record(
            course_id, session_id, record.descriptor.fingerprint, payload, interruption
        )

    def _load(
        self,
        course_id: CourseId,
        session_id: SessionId,
        fingerprint: str,
        interruption: TutorInterruptionToken,
    ) -> TutorContinuationRecord | None:
        if _interrupted(interruption):
            return None
        payload = self._store.load(course_id, session_id, fingerprint)
        if _interrupted(interruption):
            return None
        if not isinstance(payload, bytes):
            return None
        record = TutorContinuationRecord.from_bytes(payload)
        if record.descriptor.fingerprint != fingerprint:
            return None
        if (
            record.execution_context.course_id != course_id
            or record.execution_context.session_id != session_id
        ):
            return None
        return record

    def _delete(
        self,
        course_id: CourseId,
        session_id: SessionId,
        fingerprint: str,
        interruption: TutorInterruptionToken,
    ) -> None:
        if _interrupted(interruption):
            return
        self._store.delete(course_id, session_id, fingerprint)
        _interrupted(interruption)

    def _same_record(
        self,
        course_id: CourseId,
        session_id: SessionId,
        fingerprint: str,
        payload: bytes,
        interruption: TutorInterruptionToken,
    ) -> bool:
        if _interrupted(interruption):
            return False
        try:
            existing = self._store.load(
                course_id, session_id, fingerprint
            )
        except Exception:
            return False
        if _interrupted(interruption):
            return False
        return existing == payload

    @staticmethod
    def _map_terminal(
        outcome: object,
        receipt: HostRetryReceipt | None,
        capability_identity: str | None = None,
        manifest_fingerprint: str | None = None,
        gateway_retry_fingerprint: str | None = None,
        observed_host_context_sequence: int | None = None,
        completion_reference: TutorCapabilityCompletionReference | None = None,
    ) -> TutorHostRunResult:
        if isinstance(outcome, CompletedCapabilityOutcome):
            return TutorHostRunResult(
                TutorHostRunStatus.COMPLETED,
                retry_receipt=receipt if completion_reference is not None else None,
                completed_output=outcome.output,
                completion_reference=completion_reference,
                observed_host_context_sequence=observed_host_context_sequence,
            )
        if isinstance(outcome, TerminatedCapabilityOutcome):
            return TutorHostRunResult(TutorHostRunStatus.TERMINATED, receipt)
        if isinstance(outcome, CancelledCapabilityOutcome):
            return TutorHostRunResult(TutorHostRunStatus.CANCELLED, receipt)
        if isinstance(outcome, FailedCapabilityOutcome):
            return TutorHostRunResult(TutorHostRunStatus.FAILED, receipt)
        return TutorHostRunResult(TutorHostRunStatus.FAILED, receipt)


def _continuation_from_json(raw: JsonObject) -> CapabilityContinuation:
    _exact(
        raw,
        {
            "run_id",
            "capability_id",
            "capability_version",
            "manifest_fingerprint",
            "authority_fingerprint",
            "retry_identity_fingerprint",
            "definition_fingerprint",
            "checkpoint_fingerprint",
            "dialogue_step_id",
            "next_step_index",
            "inputs",
            "pins",
            "read_dependencies",
        },
        "continuation",
    )
    return CapabilityContinuation(
        RunId(_string(raw, "run_id")),
        TutorCapabilityId(_string(raw, "capability_id")),
        SemanticVersion.parse(_string(raw, "capability_version")),
        _string(raw, "manifest_fingerprint"),
        _string(raw, "authority_fingerprint"),
        _string(raw, "retry_identity_fingerprint"),
        _string(raw, "definition_fingerprint"),
        _string(raw, "checkpoint_fingerprint"),
        _string(raw, "dialogue_step_id"),
        _integer(raw, "next_step_index"),
        _object(raw["inputs"], "continuation inputs"),
        _pins_from_json(_object(raw["pins"], "continuation pins")),
        tuple(
            _dependency_from_json(item)
            for item in _array(raw["read_dependencies"], "read_dependencies")
        ),
    )


def _pins_from_json(raw: JsonObject) -> VersionPins:
    _exact(
        raw,
        {"skill", "playbook", "prompt", "tool_behaviors", "model_adapter", "state_contract"},
        "version pins",
    )

    def reference(value: JsonValue, name: str) -> ArtifactReference:
        item = _object(value, name)
        _exact(item, {"id", "version"}, name)
        return ArtifactReference(
            _string(item, "id"), SemanticVersion.parse(_string(item, "version"))
        )

    behaviors = []
    for item in _array(raw["tool_behaviors"], "tool_behaviors"):
        behavior = _object(item, "tool behavior")
        _exact(behavior, {"name", "version"}, "tool behavior")
        behaviors.append(
            ToolBehaviorPin(
                _string(behavior, "name"),
                SemanticVersion.parse(_string(behavior, "version")),
            )
        )
    return VersionPins(
        reference(raw["skill"], "skill"),
        reference(raw["playbook"], "playbook"),
        reference(raw["prompt"], "prompt"),
        tuple(behaviors),
        reference(raw["model_adapter"], "model_adapter"),
        reference(raw["state_contract"], "state_contract"),
    )


def _dependency_from_json(value: JsonValue) -> ReadDependency:
    raw = _object(value, "read dependency")
    _exact(raw, {"kind", "id", "version"}, "read dependency")
    return ReadDependency(
        _string(raw, "kind"), _string(raw, "id"), _string(raw, "version")
    )


def _descriptor_from_json(raw: JsonObject) -> PendingContinuationDescriptor:
    _exact(
        raw,
        {
            "fingerprint",
            "capability_identity",
            "dialogue_step_id",
            "dialogue_request",
            "response_schema",
        },
        "pending continuation descriptor",
    )
    return PendingContinuationDescriptor(
        _string(raw, "fingerprint"),
        _string(raw, "capability_identity"),
        _string(raw, "dialogue_step_id"),
        _string(raw, "dialogue_request"),
        _object(raw["response_schema"], "response_schema"),
    )


def _canonical_bytes(value: JsonObject) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    ).encode("utf-8")


def _canonical_object(data: bytes, name: str) -> JsonObject:
    try:
        decoded = json.loads(data)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ValueError(f"{name} is not canonical JSON") from error
    if not isinstance(decoded, dict):
        raise ValueError(f"{name} must be a JSON object")
    value = freeze_object(decoded)
    if _canonical_bytes(value) != data:
        raise ValueError(f"{name} bytes are not canonical")
    return value


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _object(value: JsonValue, name: str) -> JsonObject:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    return value


def _array(value: JsonValue, name: str) -> tuple[JsonValue, ...]:
    if not isinstance(value, tuple):
        raise ValueError(f"{name} must be an array")
    return value


def _string(value: Mapping[str, JsonValue], key: str) -> str:
    item = value.get(key)
    if not isinstance(item, str):
        raise ValueError(f"{key} must be a string")
    return item


def _integer(value: Mapping[str, JsonValue], key: str) -> int:
    item = value.get(key)
    if type(item) is not int:
        raise ValueError(f"{key} must be an integer")
    return item


def _exact(value: Mapping[str, JsonValue], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} has an invalid field set")


def _interrupted(token: TutorInterruptionToken) -> bool:
    return bool(token.is_interrupted())


class _NeverInterrupted:
    def is_interrupted(self) -> bool:
        return False


def _interrupted_result(
    record: TutorContinuationRecord | None = None,
    receipt: HostRetryReceipt | None = None,
) -> TutorHostRunResult:
    return TutorHostRunResult(
        TutorHostRunStatus.INTERRUPTED,
        receipt,
        pending_continuation=None if record is None else record.descriptor,
    )


def _failed(
    receipt: HostRetryReceipt | None = None,
    failure_reason: str | None = None,
) -> TutorHostRunResult:
    return TutorHostRunResult(
        TutorHostRunStatus.FAILED, receipt, failure_reason=failure_reason
    )


def _budget(failure_reason: str | None = None) -> TutorHostRunResult:
    return TutorHostRunResult(
        TutorHostRunStatus.BUDGET_EXHAUSTED, failure_reason=failure_reason
    )


def _require_text(value: str, name: str, maximum: int) -> None:
    if not isinstance(value, str) or not value or value != value.strip() or len(value) > maximum:
        raise ValueError(f"{name} must be bounded non-blank trimmed text")


def _require_opaque(value: str, name: str) -> None:
    _require_text(value, name, 256)
    if "/" in value or "\\" in value or "://" in value or value in {".", ".."}:
        raise ValueError(f"{name} must be opaque and path-free")


def _require_sha256(value: str, name: str) -> None:
    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise ValueError(f"{name} must be a lowercase SHA-256")


__all__ = [
    "MAX_HOST_RETRY_ATTEMPTS",
    "RetryableTutorDecisionError",
    "ScriptedDecision",
    "ScriptedDecisionError",
    "ScriptedTutorDecisionPort",
    "TutorCompletionHandoff",
    "TutorCompletionHandoffState",
    "TutorContinuationRecord",
    "TutorHostLimits",
    "TutorHostRunResult",
    "TutorHostRunStatus",
    "TutorHostRunner",
    "completion_handoff_key",
]
