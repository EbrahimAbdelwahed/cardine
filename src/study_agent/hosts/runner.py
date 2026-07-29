"""Bounded, provider-neutral orchestration for an external tutor host.

The runner is deliberately operational: canonical study state remains owned by
the capability gateway and its existing event services.  This module owns only
the decision boundary, host receipts, and an opaque continuation record.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

from study_agent.capabilities import (
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
    PendingContinuationDescriptor,
    StartCapabilityDecision,
    StopDecision,
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

        if self.status is TutorHostRunStatus.COMPLETED:
            if (
                self.retry_receipt is not None
                or self.learner_text is not None
                or self.pending_continuation is not None
                or self.presentation_receipt is not None
            ):
                raise ValueError("completed result may expose output only")
        elif self.status is TutorHostRunStatus.SUSPENDED:
            if (
                self.pending_continuation is None
                or self.learner_text is not None
                or self.completed_output is not None
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
            ):
                raise ValueError("question/message result requires bounded text only")
        elif self.status is TutorHostRunStatus.INTERRUPTED:
            if (
                self.learner_text is not None
                or self.completed_output is not None
                or self.presentation_receipt is not None
            ):
                raise ValueError("interrupted result cannot expose text or output")
        elif (
            self.learner_text is not None
            or self.completed_output is not None
            or self.pending_continuation is not None
            or self.presentation_receipt is not None
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

            try:
                decision = await self._decide(context, interruption)
            except RetryableTutorDecisionError:
                return _budget()
            except _DecisionBudgetExhausted:
                return _budget()
            except Exception:
                return (
                    _interrupted_result(selected, retry_receipt)
                    if _interrupted(interruption)
                    else _failed()
                )
            if _interrupted(interruption):
                return _interrupted_result(selected, retry_receipt)
            decisions += 1
            try:
                validate_decision(decision, context)
            except (TypeError, ValueError):
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

            retry_action = None
            if isinstance(decision, StartCapabilityDecision):
                try:
                    capability_id = TutorCapabilityId(decision.capability_id)
                except ValueError:
                    return _failed()
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
            return self._map_terminal(outcome, retry_action)

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
        outcome: object, receipt: HostRetryReceipt | None
    ) -> TutorHostRunResult:
        if isinstance(outcome, CompletedCapabilityOutcome):
            return TutorHostRunResult(
                TutorHostRunStatus.COMPLETED, completed_output=outcome.output
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


def _interrupted_result(
    record: TutorContinuationRecord | None = None,
    receipt: HostRetryReceipt | None = None,
) -> TutorHostRunResult:
    return TutorHostRunResult(
        TutorHostRunStatus.INTERRUPTED,
        receipt,
        pending_continuation=None if record is None else record.descriptor,
    )


def _failed(receipt: HostRetryReceipt | None = None) -> TutorHostRunResult:
    return TutorHostRunResult(TutorHostRunStatus.FAILED, receipt)


def _budget() -> TutorHostRunResult:
    return TutorHostRunResult(TutorHostRunStatus.BUDGET_EXHAUSTED)


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
    "TutorContinuationRecord",
    "TutorHostLimits",
    "TutorHostRunResult",
    "TutorHostRunStatus",
    "TutorHostRunner",
]
