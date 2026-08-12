"""Durable orchestration for provider-neutral adaptive tutor turns.

This module is the application owner for the small conversation tracer.  It
does not append events itself: learner interactions and validated tutor
presentations remain owned by :class:`SessionTurnService`.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass, replace
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from cardine.hosts import (
    PendingContinuationDescriptor,
    TutorCompletionHandoff,
    TutorCompletionHandoffState,
    TutorContinuationRecord,
    TutorHostRunner,
    TutorHostRunResult,
    TutorHostRunStatus,
    TutorPresentationReceipt,
    completion_handoff_key,
)
from study_agent.domain import (
    MAX_TUTOR_PRESENTATION_TEXT,
    CorrelationId,
    CourseId,
    ExecutionContext,
    InteractionId,
    InteractionRecord,
    PrincipalKind,
    SessionId,
    TutorPresentationKind,
    TutorPresentationRecord,
    TutorSnapshotV1,
)
from study_agent.domain._validation import require_text
from study_agent.ports import (
    SessionViewPort,
    TutorCompletionHandoffStore,
    TutorContinuationStore,
    TutorPresentationViewPort,
    TutorSnapshotPort,
)
from study_agent.sessions import (
    IdempotencyConflictError,
    RetryableSessionConflictError,
    SessionCommandError,
    SessionTurnService,
)
from study_agent.state import canonical_json_bytes

from .capability_completion import (
    CapabilityCompletionHandlerRegistry,
)

MAX_LEARNER_TURN_CHARS = 4_000
_FALLBACK_TERMINAL_STATUSES = frozenset(
    {
        TutorHostRunStatus.COMPLETED,
        TutorHostRunStatus.TERMINATED,
        TutorHostRunStatus.CANCELLED,
        TutorHostRunStatus.FAILED,
        TutorHostRunStatus.STOPPED,
        TutorHostRunStatus.INTERRUPTED,
        TutorHostRunStatus.BUDGET_EXHAUSTED,
    }
)
_TRANSIENT_FAILURE_REASONS = frozenset(
    {"rate_limited", "timeout", "unavailable", "model_unavailable"}
)


@dataclass(frozen=True, slots=True)
class _TerminalSettlement:
    status: TutorHostRunStatus
    fallback_message: str | None


class ConversationTurnErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RETRYABLE_CONFLICT = "retryable_conflict"
    FAILED = "failed"
    INTERRUPTED = "interrupted"
    INCOMPATIBLE_RUNTIME = "incompatible_runtime"
    CONSENT_REQUIRED = "consent_required"


class ConversationTurnError(RuntimeError):
    """Stable application error for transport-neutral conversation commands."""

    def __init__(
        self,
        code: ConversationTurnErrorCode,
        message: str,
        *,
        failure_reason: str | None = None,
        learner_persisted: bool = False,
    ) -> None:
        if not isinstance(code, ConversationTurnErrorCode):
            raise TypeError("conversation error code must use ConversationTurnErrorCode")
        require_text(message, "conversation error message")
        if failure_reason is not None and failure_reason not in {
            "authentication",
            "model_unavailable",
            "endpoint_incompatible",
            "rate_limited",
            "timeout",
            "protocol_error",
            "unavailable",
            "consent_required",
        }:
            raise ValueError("conversation failure reason is invalid")
        self.code = code
        self.failure_reason = failure_reason
        self.learner_persisted = learner_persisted
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ConversationTurnCommand:
    """One new learner entry against an expected course-stream sequence."""

    content: str
    context: ExecutionContext
    expected_sequence: int

    def __post_init__(self) -> None:
        _validate_content(self.content)
        if not isinstance(self.context, ExecutionContext):
            raise TypeError("conversation command context is invalid")
        _validate_sequence(self.expected_sequence)
        if self.context.session_id is None:
            raise ValueError("conversation command requires a session context")
        if self.context.idempotency_key is None:
            raise ValueError("conversation command requires a server-owned request identity")

    @property
    def request_id(self) -> str:
        if self.context.idempotency_key is None:  # pragma: no cover - guarded above
            raise ValueError("conversation command requires a request identity")
        return self.context.idempotency_key


@dataclass(frozen=True, slots=True)
class ConversationTurnResult:
    """Fresh canonical state returned with the presentation for this learner turn."""

    snapshot: TutorSnapshotV1
    learner_turn: InteractionRecord
    presentations: tuple[TutorPresentationRecord, ...]
    status: TutorHostRunStatus
    selected_presentation: TutorPresentationRecord
    pending_continuation: PendingContinuationDescriptor | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.snapshot, TutorSnapshotV1):
            raise TypeError("conversation result snapshot is invalid")
        if not isinstance(self.learner_turn, InteractionRecord):
            raise TypeError("conversation result learner turn is invalid")
        if self.learner_turn.kind.value != "human":
            raise ValueError("conversation result learner turn must be human")
        object.__setattr__(self, "presentations", tuple(self.presentations))
        if any(not isinstance(item, TutorPresentationRecord) for item in self.presentations):
            raise TypeError("conversation result presentations are invalid")
        if not isinstance(self.selected_presentation, TutorPresentationRecord):
            raise TypeError("conversation result requires a selected presentation")
        if self.selected_presentation not in self.presentations:
            raise ValueError("selected presentation is missing from canonical history")
        if not isinstance(self.status, TutorHostRunStatus):
            raise TypeError("conversation result status is invalid")
        if self.pending_continuation is not None and not isinstance(
            self.pending_continuation, PendingContinuationDescriptor
        ):
            raise TypeError("conversation result pending continuation is invalid")

    @property
    def tutor_snapshot(self) -> TutorSnapshotV1:
        return self.snapshot

    @property
    def high_water_sequence(self) -> int:
        return self.snapshot.high_water_sequence

    @property
    def presentation(self) -> TutorPresentationRecord:
        """The presentation produced by (or already committed for) this command."""

        return self.selected_presentation


class _InterruptionToken(Protocol):
    def is_interrupted(self) -> bool: ...


class _NeverInterrupted:
    def is_interrupted(self) -> bool:
        return False


class ConversationTurnApplication:
    """Coordinate learner recording, host execution, and receipt commits."""

    def __init__(
        self,
        turns: SessionTurnService,
        runner: TutorHostRunner,
        snapshots: TutorSnapshotPort,
        sessions: SessionViewPort,
        presentations: TutorPresentationViewPort,
        continuation_store: TutorContinuationStore | None = None,
        *,
        service_principal_id: str = "study-agent-tutor-host",
        completion_handlers: CapabilityCompletionHandlerRegistry | None = None,
        completion_handoff_store: TutorCompletionHandoffStore | None = None,
        fallback_message_policy: Callable[[TutorHostRunStatus, str | None], str],
    ) -> None:
        self._turns = turns
        self._runner = runner
        self._snapshots = snapshots
        self._sessions = sessions
        self._presentations = presentations
        if (
            continuation_store is None
            or getattr(runner, "continuation_store", None) is not continuation_store
        ):
            raise TypeError(
                "conversation application requires the runner's exact continuation store"
            )
        self._continuations = continuation_store
        runner_handoffs = getattr(runner, "completion_handoff_store", None)
        if (
            completion_handoff_store is not None
            and runner_handoffs is not None
            and completion_handoff_store is not runner_handoffs
        ):
            raise TypeError(
                "conversation application requires the runner's exact completion handoff store"
            )
        self._completion_handoffs = (
            runner_handoffs
            if runner_handoffs is not None
            else completion_handoff_store
        )
        self._completion_handlers = completion_handlers or CapabilityCompletionHandlerRegistry()
        if not callable(fallback_message_policy):
            raise TypeError("conversation application requires a fallback message policy")
        self._fallback_message_policy = fallback_message_policy
        require_text(service_principal_id, "service_principal_id")
        self._service_principal_id = service_principal_id

    async def turn(
        self,
        command: ConversationTurnCommand,
        *,
        interruption: _InterruptionToken | None = None,
    ) -> ConversationTurnResult:
        """Run one direct learner turn, returning only validated presentations."""

        if not isinstance(command, ConversationTurnCommand):
            raise TypeError("conversation turn requires ConversationTurnCommand")
        return await self._execute(
            command,
            pending_fingerprint=None,
            interruption=interruption or _NeverInterrupted(),
        )

    async def verify_model_readiness(self, course_id: CourseId, session_id: SessionId) -> None:
        """Probe the production tutor decision path without recording a turn."""

        await self._runner.verify_model_readiness(course_id, session_id)

    async def resume(
        self,
        pending_fingerprint: str,
        command: ConversationTurnCommand,
        *,
        interruption: _InterruptionToken | None = None,
    ) -> ConversationTurnResult:
        """Resume an opaque pending continuation with a new learner response."""

        _validate_fingerprint(pending_fingerprint)
        if not isinstance(command, ConversationTurnCommand):
            raise TypeError("conversation resume requires ConversationTurnCommand")
        return await self._execute(
            command,
            pending_fingerprint=pending_fingerprint,
            interruption=interruption or _NeverInterrupted(),
        )

    async def resume_continuation(
        self,
        pending_fingerprint: str,
        command: ConversationTurnCommand,
        *,
        interruption: _InterruptionToken | None = None,
    ) -> ConversationTurnResult:
        """Explicit alias used by HTTP/application compositions."""

        return await self.resume(
            pending_fingerprint, command, interruption=interruption
        )

    async def execute(
        self,
        command: ConversationTurnCommand,
        *,
        interruption: _InterruptionToken | None = None,
    ) -> ConversationTurnResult:
        """Alias matching other application service entry points."""

        return await self.turn(command, interruption=interruption)

    async def _execute(
        self,
        command: ConversationTurnCommand,
        *,
        pending_fingerprint: str | None,
        interruption: _InterruptionToken,
    ) -> ConversationTurnResult:
        context = command.context
        session_id = context.session_id
        if session_id is None:  # pragma: no cover - command validation
            raise ConversationTurnError(
                ConversationTurnErrorCode.INVALID_REQUEST,
                "conversation command requires a session",
            )
        learner_key = _identity(
            context.course_id, session_id, command.request_id, "learner"
        )
        host_turn_id = _host_turn_id(context.course_id, session_id, command.request_id)
        presentation_key = _identity(
            context.course_id, session_id, command.request_id, "presentation"
        )
        learner_context = replace(context, idempotency_key=learner_key)
        service_context = replace(
            context,
            principal_kind=PrincipalKind.SERVICE,
            principal_id=self._service_principal_id,
            idempotency_key=presentation_key,
        )
        learner_persisted = False
        try:
            existing_presentation = self._existing_presentation(
                context.course_id, session_id, host_turn_id, presentation_key
            )
            existing_learner = self._existing_learner(
                context.course_id, session_id, learner_context
            )
            terminal_settlement = self._terminal_settlement(
                context.course_id,
                session_id,
                host_turn_id,
                command.content,
                pending_fingerprint,
            )
            if terminal_settlement is not None:
                terminal_status = terminal_settlement.status
                if existing_learner is None:
                    raise ConversationTurnError(
                        ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                        "terminal conversation receipt has no learner turn",
                    )
                if existing_learner.content != command.content:
                    raise ConversationTurnError(
                        ConversationTurnErrorCode.CONFLICT,
                        "conversation request identity conflicts with learner content",
                    )
                if existing_presentation is None:
                    existing_presentation = self._record_fallback_presentation(
                        context.course_id,
                        session_id,
                        host_turn_id,
                        command.content,
                        pending_fingerprint,
                        terminal_status,
                        existing_learner,
                        service_context,
                        presentation_key,
                        failure_reason=None,
                        fallback_message=terminal_settlement.fallback_message,
                    )
                elif pending_fingerprint is not None and self._continuations is not None:
                    with suppress(KeyError, OSError, RuntimeError, ValueError):
                        self._continuations.delete(
                            context.course_id, session_id, pending_fingerprint
                        )
                return self._result(
                    context.course_id,
                    session_id,
                    existing_learner,
                    terminal_status,
                    existing_presentation,
                )
            if existing_presentation is not None:
                if existing_learner is None:
                    # A presentation without its learner linkage is corrupt; do
                    # not synthesize a new canonical turn around it.
                    raise ConversationTurnError(
                        ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                        "canonical tutor presentation has no learner turn",
                    )
                if existing_learner.content != command.content:
                    raise ConversationTurnError(
                        ConversationTurnErrorCode.CONFLICT,
                        "conversation request identity conflicts with learner content",
                    )
                return self._result(
                    context.course_id,
                    session_id,
                    existing_learner,
                    _status_for_presentation(existing_presentation),
                    existing_presentation,
                )

            # Resolve the canonical command identity before validating the
            # operational continuation.  This makes an exact retry of a
            # successfully resolved response independent of whether the
            # runner already removed its operational record.  New resumes,
            # however, must prove the continuation is still active before
            # appending a learner turn or invoking the host.
            if pending_fingerprint is not None:
                self._require_pending(context.course_id, session_id, pending_fingerprint)

            current = self._snapshots.get(context.course_id, session_id)
            if (
                current.high_water_sequence != command.expected_sequence
                and existing_learner is None
            ):
                raise ConversationTurnError(
                    ConversationTurnErrorCode.RETRYABLE_CONFLICT,
                    "canonical session state advanced; retry safely",
                )
            learner = self._turns.record_learner_turn(
                command.content, learner_context, command.expected_sequence
            )
            learner_persisted = True
            try:
                host_result = await self._runner.run(
                    context.course_id,
                    session_id,
                    host_turn_id,
                    interruption,
                    pending_fingerprint=pending_fingerprint,
                )
            except Exception:
                # Once the learner turn exists, an operational host failure is
                # represented by a safe canonical chat outcome.  Provider and
                # runtime details remain outside learner-visible state.
                host_result = TutorHostRunResult(TutorHostRunStatus.FAILED)
            if host_result.failure_reason == "consent_required":
                raise ConversationTurnError(
                    ConversationTurnErrorCode.CONSENT_REQUIRED,
                    "provider consent is required before tutor execution",
                    learner_persisted=learner_persisted,
                )
            if host_result.failure_reason in _TRANSIENT_FAILURE_REASONS:
                # Only the closed transient provider set is retryable. Keep
                # the learner fact committed, but do not settle a terminal
                # fallback: an exact retry reuses this learner turn and may
                # run the host again.
                raise _host_error(host_result)
            if host_result.status is TutorHostRunStatus.COMPLETED:
                presentation = self._recover_completion_presentation(
                    context.course_id,
                    session_id,
                    host_result,
                    learner.id,
                )
                if presentation is None:
                    presentation = self._record_fallback_presentation(
                        context.course_id,
                        session_id,
                        host_turn_id,
                        command.content,
                        pending_fingerprint,
                        host_result.status,
                        learner,
                        service_context,
                        presentation_key,
                        failure_reason=host_result.failure_reason,
                    )
                if pending_fingerprint is not None and self._continuations is not None:
                    with suppress(KeyError, OSError, RuntimeError, ValueError):
                        self._continuations.delete(
                            context.course_id, session_id, pending_fingerprint
                        )
                return self._result(
                    context.course_id,
                    session_id,
                    learner,
                    TutorHostRunStatus.COMPLETED,
                    presentation,
                )
            if host_result.status is TutorHostRunStatus.TERMINATED:
                presentation = self._record_fallback_presentation(
                    context.course_id,
                    session_id,
                    host_turn_id,
                    command.content,
                    pending_fingerprint,
                    host_result.status,
                    learner,
                    service_context,
                    presentation_key,
                    failure_reason=host_result.failure_reason,
                )
                return self._result(
                    context.course_id,
                    session_id,
                    learner,
                    TutorHostRunStatus.TERMINATED,
                    presentation,
                )
            if host_result.status in {
                TutorHostRunStatus.ASSISTANT_MESSAGE,
                TutorHostRunStatus.NEEDS_LEARNER_INPUT,
                TutorHostRunStatus.SUSPENDED,
            }:
                receipt = host_result.presentation_receipt
                if receipt is None:
                    presentation = self._record_fallback_presentation(
                        context.course_id,
                        session_id,
                        host_turn_id,
                        command.content,
                        pending_fingerprint,
                        TutorHostRunStatus.FAILED,
                        learner,
                        service_context,
                        presentation_key,
                        failure_reason=host_result.failure_reason,
                    )
                    return self._result(
                        context.course_id,
                        session_id,
                        learner,
                        TutorHostRunStatus.FAILED,
                        presentation,
                    )
                self._turns.record_tutor_presentation(
                    context=service_context,
                    receipt=receipt,
                    in_reply_to_interaction_id=learner.id,
                    expected_sequence=receipt.observed_host_context_sequence,
                )
                if (
                    pending_fingerprint is not None
                    and host_result.status is not TutorHostRunStatus.SUSPENDED
                    and self._continuations is not None
                ):
                    # The runner normally removes the operational record when
                    # resolving a continuation.  Repeating the delete after
                    # the canonical receipt commit also makes the application
                    # boundary safe for injected host runners and exact
                    # retries; the canonical presentation history remains.
                    with suppress(OSError, RuntimeError, ValueError):
                        self._continuations.delete(
                            context.course_id, session_id, pending_fingerprint
                        )
                presentation = self._existing_presentation(
                    context.course_id, session_id, host_turn_id, presentation_key
                )
                if presentation is None:
                    raise ConversationTurnError(
                        ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                        "canonical tutor presentation was not committed",
                    )
                return self._result(
                    context.course_id,
                    session_id,
                    learner,
                    host_result.status,
                    presentation,
                )
            if host_result.status is TutorHostRunStatus.IN_PROGRESS:
                raise _host_error(host_result)
            presentation = self._record_fallback_presentation(
                context.course_id,
                session_id,
                host_turn_id,
                command.content,
                pending_fingerprint,
                host_result.status,
                learner,
                service_context,
                presentation_key,
                failure_reason=host_result.failure_reason,
            )
            return self._result(
                context.course_id,
                session_id,
                learner,
                host_result.status,
                presentation,
            )
        except ConversationTurnError as error:
            error.learner_persisted = error.learner_persisted or learner_persisted
            raise
        except IdempotencyConflictError as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.CONFLICT,
                "conversation request identity conflicts with canonical state",
                learner_persisted=learner_persisted,
            ) from error
        except RetryableSessionConflictError as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.RETRYABLE_CONFLICT,
                "canonical session state advanced; retry safely",
                learner_persisted=learner_persisted,
            ) from error
        except SessionCommandError as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.CONFLICT,
                "conversation command cannot be applied to this session",
                learner_persisted=learner_persisted,
            ) from error
        except (LookupError, OSError, ValueError, RuntimeError) as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "conversation runtime is unavailable",
                learner_persisted=learner_persisted,
            ) from error

    def _terminal_settlement(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        content: str,
        pending_fingerprint: str | None,
    ) -> _TerminalSettlement | None:
        if self._completion_handoffs is None:
            return None
        key = _terminal_receipt_key(course_id, session_id, host_turn_id)
        try:
            payload = self._completion_handoffs.load(key)
        except KeyError:
            return None
        return _decode_terminal_receipt(
            payload,
            course_id,
            session_id,
            host_turn_id,
            content,
            pending_fingerprint,
        )

    def _record_terminal_status(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        content: str,
        pending_fingerprint: str | None,
        status: TutorHostRunStatus,
        fallback_message: str,
    ) -> None:
        if self._completion_handoffs is None:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "terminal conversation receipt store is unavailable",
            )
        key = _terminal_receipt_key(course_id, session_id, host_turn_id)
        payload = _terminal_receipt_bytes(
            course_id,
            session_id,
            host_turn_id,
            content,
            pending_fingerprint,
            status,
            fallback_message,
        )
        if self._completion_handoffs.create(key, payload):
            return
        try:
            existing = self._completion_handoffs.load(key)
        except KeyError as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "terminal conversation receipt could not be persisted",
            ) from error
        try:
            existing_settlement = _decode_terminal_receipt(
                existing,
                course_id,
                session_id,
                host_turn_id,
                content,
                pending_fingerprint,
            )
        except (TypeError, ValueError):
            existing_settlement = None
        if existing_settlement is None or (
            existing_settlement.status is not status
            or existing_settlement.fallback_message not in {None, fallback_message}
        ):
            raise ConversationTurnError(
                ConversationTurnErrorCode.CONFLICT,
                "terminal conversation receipt conflicts with canonical state",
            )

    def _result(
        self,
        course_id: CourseId,
        session_id: SessionId,
        learner: InteractionRecord,
        status: TutorHostRunStatus,
        presentation: TutorPresentationRecord,
    ) -> ConversationTurnResult:
        snapshot = self._snapshots.get(course_id, session_id)
        rows = self._presentations.presentations(course_id, session_id)
        return ConversationTurnResult(
            snapshot=snapshot,
            learner_turn=learner,
            presentations=rows,
            status=status,
            selected_presentation=presentation,
            pending_continuation=self._active_pending(course_id, session_id, rows),
        )

    def _record_fallback_presentation(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        learner_content: str,
        pending_fingerprint: str | None,
        status: TutorHostRunStatus,
        learner: InteractionRecord,
        service_context: ExecutionContext,
        presentation_key: str,
        *,
        failure_reason: str | None,
        fallback_message: str | None = None,
    ) -> TutorPresentationRecord:
        """Commit a safe visible outcome for a terminal host path."""

        if status not in _FALLBACK_TERMINAL_STATUSES:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "host status cannot be converted to a fallback presentation",
                learner_persisted=True,
            )
        resolved_message = self._fallback_message_policy(status, failure_reason)
        _validate_fallback_message(resolved_message)
        if fallback_message is not None and fallback_message != resolved_message:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "terminal fallback policy does not match canonical settlement",
                learner_persisted=True,
            )
        self._record_terminal_status(
            course_id,
            session_id,
            host_turn_id,
            learner_content,
            pending_fingerprint,
            status,
            resolved_message,
        )
        observed_sequence = self._snapshots.get(course_id, session_id).high_water_sequence
        receipt = TutorPresentationReceipt(
            host_turn_id=host_turn_id,
            kind=TutorPresentationKind.ASSISTANT_MESSAGE,
            content=resolved_message,
            observed_host_context_sequence=observed_sequence,
            host_context_fingerprint=sha256(
                f"cardine-fallback-context-v1\0{course_id}\0{session_id}\0{host_turn_id}".encode()
            ).hexdigest(),
            decision_fingerprint=sha256(
                f"cardine-fallback-decision-v1\0{host_turn_id}\0{status.value}".encode()
            ).hexdigest(),
        )
        self._turns.record_tutor_presentation(
            context=service_context,
            receipt=receipt,
            in_reply_to_interaction_id=learner.id,
            expected_sequence=observed_sequence,
        )
        if pending_fingerprint is not None and self._continuations is not None:
            with suppress(KeyError, OSError, RuntimeError, ValueError):
                self._continuations.delete(course_id, session_id, pending_fingerprint)
        presentation = self._existing_presentation(
            course_id, session_id, host_turn_id, presentation_key
        )
        if presentation is None:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "fallback tutor presentation was not committed",
                learner_persisted=True,
            )
        return presentation

    def _recover_completion_presentation(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_result: TutorHostRunResult,
        learner_id: InteractionId,
    ) -> TutorPresentationRecord | None:
        reference = host_result.completion_reference
        retry = host_result.retry_receipt
        observed_sequence = host_result.observed_host_context_sequence
        if reference is None or retry is None or observed_sequence is None:
            return None
        handoff: TutorCompletionHandoff | None = None
        if self._completion_handoffs is not None:
            try:
                payload = self._completion_handoffs.load(
                    completion_handoff_key(course_id, session_id, retry.host_turn_id)
                )
                handoff = TutorCompletionHandoff.from_bytes(payload)
                if (
                    handoff.state is not TutorCompletionHandoffState.COMPLETED
                    or handoff.completion_reference != reference
                    or handoff.observed_host_context_sequence != observed_sequence
                ):
                    return None
            except (KeyError, OSError, RuntimeError, TypeError, ValueError):
                return None
        recovery_context = None if handoff is None else handoff.execution_context
        try:
            product = self._completion_handlers.recover(reference, recovery_context)
        except (LookupError, OSError, RuntimeError, TypeError, ValueError):
            return None
        if product is None:
            return None
        context = ExecutionContext(
            PrincipalKind.SERVICE,
            self._service_principal_id,
            course_id,
            # The canonical service command identity remains the host-turn
            # identity; this branch never copies capability output directly.
            CorrelationId(f"cardine-completion-{retry.host_turn_id}"),
            frozenset({"study:ask"}),
            session_id,
            idempotency_key=_identity(
                course_id, session_id, retry.host_turn_id, "completion-presentation"
            ),
        )
        receipt = TutorPresentationReceipt(
            host_turn_id=retry.host_turn_id,
            kind=TutorPresentationKind.ASSISTANT_MESSAGE,
            content=product.content,
            observed_host_context_sequence=observed_sequence,
            host_context_fingerprint=(
                retry.context_fingerprint
                if handoff is None
                else handoff.context_fingerprint
            ),
            decision_fingerprint=retry.action_fingerprint,
        )
        self._turns.record_tutor_presentation(
            context=context,
            receipt=receipt,
            in_reply_to_interaction_id=learner_id,
            expected_sequence=receipt.observed_host_context_sequence,
        )
        return self._existing_presentation(
            course_id,
            session_id,
            retry.host_turn_id,
            context.idempotency_key or "",
        )

    def _existing_learner(
        self, course_id: CourseId, session_id: SessionId, context: ExecutionContext
    ) -> InteractionRecord | None:
        from study_agent.domain import learner_interaction_id_for

        if context.idempotency_key is None:
            return None
        identity = learner_interaction_id_for(course_id, session_id, context.idempotency_key)
        return next(
            (
                item
                for item in self._sessions.interactions(course_id, session_id)
                if item.id == identity
            ),
            None,
        )

    def _existing_presentation(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        presentation_key: str,
    ) -> TutorPresentationRecord | None:
        rows = tuple(self._presentations.presentations(course_id, session_id))
        matches = tuple(
            item
            for item in rows
            if item.host_turn_id == host_turn_id or item.idempotency_key == presentation_key
        )
        if not matches:
            return None
        if len(matches) != 1:
            raise ConversationTurnError(
                ConversationTurnErrorCode.CONFLICT,
                "conversation request identity names multiple presentations",
            )
        return matches[0]

    def _require_pending(
        self, course_id: CourseId, session_id: SessionId | None, fingerprint: str
    ) -> TutorPresentationRecord:
        if session_id is None:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INVALID_REQUEST,
                "continuation response requires a session",
            )
        rows = self._presentations.presentations(course_id, session_id)
        row = next(
            (
                item
                for item in rows
                if item.kind is TutorPresentationKind.CONTINUATION_REQUEST
                and item.continuation_fingerprint == fingerprint
            ),
            None,
        )
        if row is None:
            raise ConversationTurnError(
                ConversationTurnErrorCode.NOT_FOUND,
                "continuation was not found",
            )
        if self._continuations is None:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "continuation storage is unavailable",
            )
        try:
            payload = self._continuations.load(course_id, session_id, fingerprint)
            operational = TutorContinuationRecord.from_bytes(payload).descriptor
            if (
                operational.fingerprint != row.continuation_fingerprint
                or operational.capability_identity != row.capability_identity
                or operational.dialogue_request != row.content
                or operational.response_schema != row.response_schema
            ):
                raise ConversationTurnError(
                    ConversationTurnErrorCode.CONFLICT,
                    "continuation descriptor does not match canonical presentation",
                )
        except KeyError as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.CONFLICT,
                "continuation is no longer active",
            ) from error
        except (OSError, RuntimeError, ValueError) as error:
            raise ConversationTurnError(
                ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME,
                "continuation storage is unavailable",
            ) from error
        return row

    def _active_pending(
        self,
        course_id: CourseId,
        session_id: SessionId,
        rows: tuple[TutorPresentationRecord, ...],
    ) -> PendingContinuationDescriptor | None:
        if self._continuations is None:
            return None
        for row in reversed(rows):
            if row.kind is not TutorPresentationKind.CONTINUATION_REQUEST:
                continue
            fingerprint = row.continuation_fingerprint
            if (
                fingerprint is None
                or row.capability_identity is None
                or row.response_schema is None
            ):
                continue
            try:
                payload = self._continuations.load(course_id, session_id, fingerprint)
                record = TutorContinuationRecord.from_bytes(payload)
                if record.descriptor.fingerprint != fingerprint:
                    continue
            except (KeyError, OSError, RuntimeError, ValueError):
                continue
            return record.descriptor
        return None


def _identity(
    course_id: CourseId, session_id: SessionId, request_id: str, domain: str
) -> str:
    return sha256(
        f"study-agent-conversation-{domain}-identity-v1\0"
        f"{course_id}\0{session_id}\0{request_id}".encode()
    ).hexdigest()


def _host_turn_id(course_id: CourseId, session_id: SessionId, request_id: str) -> str:
    return f"tutor-host-turn-sha256:{_identity(course_id, session_id, request_id, 'host-turn')}"


def _terminal_receipt_key(
    course_id: CourseId, session_id: SessionId, host_turn_id: str
) -> str:
    digest = sha256(
        f"study-agent-conversation-terminal-receipt-v1\0"
        f"{course_id}\0{session_id}\0{host_turn_id}".encode()
    ).hexdigest()
    return f"conversation-terminal-sha256:{digest}"


def _terminal_receipt_bytes(
    course_id: CourseId,
    session_id: SessionId,
    host_turn_id: str,
    content: str,
    pending_fingerprint: str | None,
    status: TutorHostRunStatus,
    fallback_message: str,
) -> bytes:
    if status not in _FALLBACK_TERMINAL_STATUSES:
        raise ValueError("terminal conversation receipt status is invalid")
    _validate_fallback_message(fallback_message)
    return canonical_json_bytes(
        {
            "schema_version": 2,
            "course_id": str(course_id),
            "session_id": str(session_id),
            "host_turn_id": host_turn_id,
            "content_sha256": sha256(content.encode()).hexdigest(),
            "pending_fingerprint": pending_fingerprint,
            "status": status.value,
            "fallback_message": fallback_message,
        }
    )


def _decode_terminal_receipt(
    payload: bytes,
    course_id: CourseId,
    session_id: SessionId,
    host_turn_id: str,
    content: str,
    pending_fingerprint: str | None,
) -> _TerminalSettlement:
    if not isinstance(payload, bytes):
        raise ValueError("terminal conversation receipt must be bytes")
    raw = json.loads(payload)
    if not isinstance(raw, dict):
        raise ValueError("terminal conversation receipt shape is invalid")
    schema_version = raw.get("schema_version")
    common_fields = {
        "schema_version",
        "course_id",
        "session_id",
        "host_turn_id",
        "content_sha256",
        "pending_fingerprint",
        "status",
    }
    expected_fields = (
        common_fields if schema_version == 1 else common_fields | {"fallback_message"}
    )
    if schema_version not in {1, 2} or set(raw) != expected_fields:
        raise ValueError("terminal conversation receipt shape is invalid")
    expected = {
        "course_id": str(course_id),
        "session_id": str(session_id),
        "host_turn_id": host_turn_id,
        "content_sha256": sha256(content.encode()).hexdigest(),
        "pending_fingerprint": pending_fingerprint,
    }
    if any(raw.get(key) != value for key, value in expected.items()):
        raise ValueError("terminal conversation receipt scope is incompatible")
    raw_status = raw.get("status")
    if not isinstance(raw_status, str):
        raise ValueError("terminal conversation receipt status is invalid")
    status = TutorHostRunStatus(raw_status)
    allowed_statuses = (
        {TutorHostRunStatus.COMPLETED, TutorHostRunStatus.TERMINATED}
        if schema_version == 1
        else _FALLBACK_TERMINAL_STATUSES
    )
    if status not in allowed_statuses:
        raise ValueError("terminal conversation receipt status is invalid")
    fallback_message = raw.get("fallback_message")
    if schema_version == 2 and not isinstance(fallback_message, str):
        raise ValueError("terminal fallback message is invalid")
    if isinstance(fallback_message, str):
        _validate_fallback_message(fallback_message)
    return _TerminalSettlement(
        status,
        fallback_message if isinstance(fallback_message, str) else None,
    )


def _validate_content(content: object) -> None:
    if not isinstance(content, str) or not content or content != content.strip():
        raise ValueError("conversation content must be bounded non-blank text")
    if len(content) > MAX_LEARNER_TURN_CHARS:
        raise ValueError("conversation content exceeds its bound")


def _validate_fallback_message(message: object) -> None:
    if not isinstance(message, str):
        raise TypeError("terminal fallback message must be text")
    require_text(message, "terminal fallback message")
    if len(message) > MAX_TUTOR_PRESENTATION_TEXT:
        raise ValueError("terminal fallback message exceeds presentation bounds")


def _validate_sequence(sequence: object) -> None:
    if type(sequence) is not int or sequence < 0:
        raise ValueError("expected_sequence must be a non-negative integer")


def _validate_fingerprint(value: object) -> None:
    if not isinstance(value, str) or len(value) != 64 or any(
        character not in "0123456789abcdef" for character in value
    ):
        raise ValueError("continuation fingerprint must be a SHA-256 digest")


def _status_for_presentation(record: TutorPresentationRecord) -> TutorHostRunStatus:
    if record.kind is TutorPresentationKind.ASSISTANT_MESSAGE:
        return TutorHostRunStatus.ASSISTANT_MESSAGE
    if record.kind is TutorPresentationKind.LEARNER_QUESTION:
        return TutorHostRunStatus.NEEDS_LEARNER_INPUT
    return TutorHostRunStatus.SUSPENDED


def _host_error(result: TutorHostRunResult) -> ConversationTurnError:
    return ConversationTurnError(
        ConversationTurnErrorCode.FAILED,
        "tutor execution is still in progress",
        failure_reason=result.failure_reason,
        learner_persisted=True,
    )


__all__ = [
    "MAX_LEARNER_TURN_CHARS",
    "ConversationTurnApplication",
    "ConversationTurnCommand",
    "ConversationTurnError",
    "ConversationTurnErrorCode",
    "ConversationTurnResult",
]
