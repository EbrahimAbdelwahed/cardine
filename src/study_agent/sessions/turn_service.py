"""Canonical commands for general learner and verified assistant turns."""

from __future__ import annotations

from datetime import datetime
from typing import cast

from study_agent.domain import (
    Actor,
    AssistantTurnRecord,
    DomainEvent,
    EventId,
    ExecutionContext,
    InteractionId,
    InteractionKind,
    InteractionRecord,
    PrincipalKind,
    RunId,
    SessionId,
    SessionStatus,
    StudySessionRecord,
    TutorPresentationRecord,
    VerifiedRunOutputRef,
    assistant_interaction_id_for,
    learner_interaction_id_for,
    session_turn_event_id_for,
    tutor_presentation_id_for,
)
from study_agent.domain._validation import JsonObject, require_text
from study_agent.hosts.contracts import TutorPresentationReceipt
from study_agent.playbooks import (
    PlaybookDefinition,
    PlaybookEngine,
    ReadDependency,
    VersionPins,
)
from study_agent.ports import (
    AssistantTurnViewPort,
    ClockPort,
    EventSequenceConflictError,
    EventStore,
    SessionViewPort,
    TutorPresentationViewPort,
)

from .events import (
    SESSION_ASSISTANT_TURN_RECORDED,
    SESSION_INTERACTION_RECORDED,
    SESSION_SCHEMA_VERSION,
    SESSION_TUTOR_PRESENTATION_RECORDED,
    assistant_turn_command_fingerprint,
    assistant_turn_recorded_payload,
    interaction_recorded_payload,
    tutor_presentation_command_fingerprint,
    tutor_presentation_recorded_payload,
)
from .service import (
    IdempotencyConflictError,
    RetryableSessionConflictError,
    SessionCommandError,
)
from .turns import verified_tutor_message


class SessionTurnService:
    def __init__(
        self,
        events: EventStore,
        clock: ClockPort,
        sessions: SessionViewPort,
        assistant_turns: AssistantTurnViewPort,
        presentations: TutorPresentationViewPort | None = None,
    ) -> None:
        self._events = events
        self._clock = clock
        self._sessions = sessions
        self._assistant_turns = assistant_turns
        self._presentations = presentations

    def record_learner_turn(
        self,
        content: str,
        context: ExecutionContext,
        expected_sequence: int,
    ) -> InteractionRecord:
        session_id, key, session = self._context(context, assistant=False)
        _expected_sequence(expected_sequence)
        require_text(content, "content")
        interaction_id = learner_interaction_id_for(context.course_id, session_id, key)
        existing = self._existing_learner(context, interaction_id)
        if existing is not None:
            if existing.kind is InteractionKind.HUMAN and existing.content == content:
                return existing
            raise IdempotencyConflictError("learner turn retry identity has different content")
        _require_active(session)
        self._expect_sequence(context, expected_sequence)
        now = self._clock.now()
        turn_event = self._event(
            context,
            SESSION_INTERACTION_RECORDED,
            interaction_recorded_payload(interaction_id, InteractionKind.HUMAN, content),
            expected_sequence + 1,
            session_turn_event_id_for(
                context.course_id, session_id, key, SESSION_INTERACTION_RECORDED
            ),
            now,
        )
        try:
            self._events.append(context.course_id, expected_sequence, (turn_event,))
        except EventSequenceConflictError as error:
            existing = self._existing_learner(context, interaction_id)
            if existing is not None:
                if existing.kind is InteractionKind.HUMAN and existing.content == content:
                    return existing
                raise IdempotencyConflictError(
                    "learner turn retry identity has different content"
                ) from error
            raise RetryableSessionConflictError(
                "course stream raced before the learner turn batch committed"
            ) from error
        return self._required_learner(context, interaction_id)

    def record_assistant_turn(
        self,
        *,
        context: ExecutionContext,
        engine: PlaybookEngine,
        run_id: RunId,
        definition: PlaybookDefinition,
        inputs: JsonObject,
        pins: VersionPins,
        expected_sequence: int,
        read_dependencies: tuple[ReadDependency, ...] = (),
    ) -> AssistantTurnRecord:
        session_id, key, session = self._context(context, assistant=True)
        _expected_sequence(expected_sequence)
        run = engine.recover(
            run_id=run_id,
            definition=definition,
            inputs=inputs,
            pins=pins,
            read_dependencies=read_dependencies,
        )
        if run.inputs.get("course_id") != str(context.course_id) or run.inputs.get(
            "session_id"
        ) != str(session_id):
            raise SessionCommandError("verified run belongs to another course or session")
        status, content, reply, output = verified_tutor_message(run)
        turn_id = assistant_interaction_id_for(
            context.course_id, session_id, run.run_id, key
        )
        command_fingerprint = assistant_turn_command_fingerprint(
            status, content, reply, output
        )
        requested = AssistantTurnRecord(
            id=turn_id,
            session_id=session_id,
            occurred_at=self._clock.now(),
            status=status,
            content=content,
            in_reply_to_interaction_id=reply,
            output=output,
            idempotency_key=key,
            command_fingerprint=command_fingerprint,
            event_id=session_turn_event_id_for(
                context.course_id, session_id, key, SESSION_ASSISTANT_TURN_RECORDED
            ),
            course_sequence=expected_sequence + 1,
        )
        existing = self._existing_assistant(context, turn_id, output, key)
        if existing is not None:
            return _same_assistant_or_conflict(existing, requested)
        _require_active(session)
        for owner in self._sessions.list_sessions(context.course_id):
            for answer in self._sessions.answers(context.course_id, owner.id):
                if answer.run_id == run.run_id:
                    raise IdempotencyConflictError(
                        "run already belongs to a grounded answer"
                    )
                if owner.id == session_id and answer.idempotency_key == key:
                    raise IdempotencyConflictError(
                        "idempotency key already belongs to a grounded answer"
                    )
            for turn in self._assistant_turns.turns(context.course_id, owner.id):
                if turn.output.run_id == run.run_id:
                    raise IdempotencyConflictError(
                        "run already belongs to an assistant turn"
                    )
                if owner.id == session_id and turn.idempotency_key == key:
                    raise IdempotencyConflictError(
                        "idempotency key already belongs to an assistant turn"
                    )
        if reply is not None:
            interaction = next(
                (
                    item
                    for item in self._sessions.interactions(context.course_id, session_id)
                    if item.id == reply
                ),
                None,
            )
            if interaction is None or interaction.kind is not InteractionKind.HUMAN:
                raise SessionCommandError(
                    "assistant reply target must be a human interaction in the session"
                )
        self._expect_sequence(context, expected_sequence)
        event = self._event(
            context,
            SESSION_ASSISTANT_TURN_RECORDED,
            assistant_turn_recorded_payload(requested),
            expected_sequence + 1,
            requested.event_id,
            requested.occurred_at,
        )
        try:
            self._events.append(context.course_id, expected_sequence, (event,))
        except EventSequenceConflictError as error:
            existing = self._existing_assistant(context, turn_id, output, key)
            if existing is not None:
                return _same_assistant_or_conflict(existing, requested)
            raise RetryableSessionConflictError(
                "course stream raced before the assistant turn committed"
            ) from error
        existing = self._existing_assistant(context, turn_id, output, key)
        if existing is None:  # pragma: no cover - projection/store contract
            raise RuntimeError("committed assistant turn is missing from the projection")
        return existing

    def record_tutor_presentation(
        self,
        *,
        context: ExecutionContext,
        receipt: TutorPresentationReceipt,
        in_reply_to_interaction_id: InteractionId | None = None,
        expected_sequence: int | None = None,
    ) -> TutorPresentationRecord:
        """Persist only a closed receipt emitted by the validated host runner."""
        if not isinstance(receipt, TutorPresentationReceipt):
            raise TypeError("record_tutor_presentation requires TutorPresentationReceipt")
        if expected_sequence is not None:
            _expected_sequence(expected_sequence)
        session_id, key, session = self._context(context, assistant=False)
        if context.principal_kind is not PrincipalKind.SERVICE:
            raise SessionCommandError("tutor presentations require service authority")
        if self._presentations is None and not hasattr(self._sessions, "presentations"):
            raise SessionCommandError("tutor presentation view is required")
        presentation_view = self._presentation_view()
        reply = in_reply_to_interaction_id
        if reply is None:
            candidate = learner_interaction_id_for(context.course_id, session_id, key)
            if any(
                item.id == candidate
                for item in self._sessions.interactions(context.course_id, session_id)
            ):
                reply = candidate
        if reply is not None:
            interaction = next(
                (
                    item
                    for item in self._sessions.interactions(context.course_id, session_id)
                    if item.id == reply
                ),
                None,
            )
            if interaction is None or interaction.kind is not InteractionKind.HUMAN:
                raise SessionCommandError(
                    "tutor presentation reply target must be human and belong to the session"
                )
        presentation_id = tutor_presentation_id_for(
            context.course_id, session_id, receipt.host_turn_id, receipt.kind.value
        )
        event_id = session_turn_event_id_for(
            context.course_id, session_id, key, SESSION_TUTOR_PRESENTATION_RECORDED
        )
        command_fingerprint = tutor_presentation_command_fingerprint(
            receipt.kind,
            receipt.content,
            reply,
            receipt.host_turn_id,
            receipt.observed_host_context_sequence,
            receipt.host_context_fingerprint,
            receipt.decision_fingerprint,
            receipt.fingerprint,
            receipt.continuation_fingerprint,
            receipt.capability_identity,
            receipt.response_schema,
        )
        requested = TutorPresentationRecord(
            id=presentation_id,
            session_id=session_id,
            occurred_at=self._clock.now(),
            kind=receipt.kind,
            content=receipt.content,
            in_reply_to_interaction_id=reply,
            host_turn_id=receipt.host_turn_id,
            observed_host_context_sequence=receipt.observed_host_context_sequence,
            host_context_fingerprint=receipt.host_context_fingerprint,
            decision_fingerprint=receipt.decision_fingerprint,
            receipt_fingerprint=receipt.fingerprint,
            continuation_fingerprint=receipt.continuation_fingerprint,
            capability_identity=receipt.capability_identity,
            response_schema=receipt.response_schema,
            idempotency_key=key,
            command_fingerprint=command_fingerprint,
            event_id=event_id,
            course_sequence=receipt.observed_host_context_sequence + 1,
        )
        existing = self._existing_presentation(presentation_view, context, requested)
        if existing is not None:
            return _same_presentation_or_conflict(existing, requested)
        _require_active(session)
        for owner in self._sessions.list_sessions(context.course_id):
            for item in presentation_view.presentations(context.course_id, owner.id):
                if owner.id == session_id and item.idempotency_key == key:
                    raise IdempotencyConflictError(
                        "idempotency key already belongs to a tutor presentation"
                    )
                if item.host_turn_id == receipt.host_turn_id and owner.id != session_id:
                    raise IdempotencyConflictError("host turn identity belongs to another session")
        self._expect_sequence(context, receipt.observed_host_context_sequence)
        event = self._event(
            context,
            SESSION_TUTOR_PRESENTATION_RECORDED,
            tutor_presentation_recorded_payload(requested),
            receipt.observed_host_context_sequence + 1,
            event_id,
            requested.occurred_at,
        )
        try:
            self._events.append(
                context.course_id,
                receipt.observed_host_context_sequence,
                (event,),
            )
        except EventSequenceConflictError as error:
            existing = self._existing_presentation(presentation_view, context, requested)
            if existing is not None:
                return _same_presentation_or_conflict(existing, requested)
            raise RetryableSessionConflictError(
                "course stream raced before the tutor presentation committed"
            ) from error
        existing = self._existing_presentation(presentation_view, context, requested)
        if existing is None:  # pragma: no cover - projection/store contract
            raise RuntimeError("committed tutor presentation is missing from the projection")
        return _same_presentation_or_conflict(existing, requested)

    def _presentation_view(self) -> TutorPresentationViewPort:
        if self._presentations is not None:
            return self._presentations
        candidate = getattr(self._sessions, "presentations", None)
        if candidate is None:
            raise SessionCommandError("tutor presentation view is required")
        return cast(TutorPresentationViewPort, candidate)

    @staticmethod
    def _existing_presentation(
        view: TutorPresentationViewPort,
        context: ExecutionContext,
        requested: TutorPresentationRecord,
    ) -> TutorPresentationRecord | None:
        candidates = tuple(
            item
            for item in view.presentations(context.course_id, requested.session_id)
            if item.id == requested.id
            or item.host_turn_id == requested.host_turn_id
            or item.idempotency_key == requested.idempotency_key
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise IdempotencyConflictError("tutor presentation retry identities disagree")
        return candidates[0]

    def _context(
        self, context: ExecutionContext, *, assistant: bool
    ) -> tuple[SessionId, str, StudySessionRecord]:
        if context.session_id is None:
            raise SessionCommandError("session turn commands require context.session_id")
        if context.idempotency_key is None:
            raise SessionCommandError("session turn commands require an idempotency key")
        allowed = (PrincipalKind.SERVICE,) if assistant else (
            PrincipalKind.HUMAN,
            PrincipalKind.SERVICE,
        )
        if context.principal_kind not in allowed:
            raise SessionCommandError("session turn command authority is not trusted")
        session = self._sessions.get_session(context.course_id, context.session_id)
        if session.course_id != context.course_id or session.id != context.session_id:
            raise SessionCommandError("execution context does not own the session")
        return context.session_id, context.idempotency_key, session

    def _existing_learner(
        self, context: ExecutionContext, interaction_id: InteractionId
    ) -> InteractionRecord | None:
        return next(
            (
                item
                for item in self._sessions.interactions(
                    context.course_id, _session_id(context)
                )
                if item.id == interaction_id
            ),
            None,
        )

    def _required_learner(
        self, context: ExecutionContext, interaction_id: InteractionId
    ) -> InteractionRecord:
        result = self._existing_learner(context, interaction_id)
        if result is None:  # pragma: no cover - projection/store contract
            raise RuntimeError("committed learner turn is missing from the projection")
        return result

    def _existing_assistant(
        self,
        context: ExecutionContext,
        turn_id: InteractionId,
        output: VerifiedRunOutputRef,
        key: str,
    ) -> AssistantTurnRecord | None:
        candidates = tuple(
            item
            for item in self._assistant_turns.turns(
                context.course_id, _session_id(context)
            )
            if item.id == turn_id
            or item.output.run_id == output.run_id
            or item.idempotency_key == key
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise IdempotencyConflictError(
                "assistant turn retry identities disagree"
            )
        return candidates[0]

    def _expect_sequence(self, context: ExecutionContext, expected: int) -> None:
        stream = self._events.read(context.course_id)
        actual = stream[-1].course_sequence if stream else 0
        if actual != expected:
            raise RetryableSessionConflictError(
                "course stream advanced before the session turn command"
            )

    def _event(
        self,
        context: ExecutionContext,
        event_type: str,
        payload: JsonObject,
        sequence: int,
        event_id: EventId,
        occurred_at: datetime,
        *,
        causation_id: EventId | None = None,
    ) -> DomainEvent:
        return DomainEvent(
            event_id=event_id,
            course_id=context.course_id,
            course_sequence=sequence,
            event_type=event_type,
            schema_version=SESSION_SCHEMA_VERSION,
            actor=Actor(context.principal_kind, context.principal_id),
            occurred_at=occurred_at,
            correlation_id=context.correlation_id,
            payload=payload,
            session_id=_session_id(context),
            causation_id=causation_id,
        )


def _same_assistant_or_conflict(
    existing: AssistantTurnRecord, requested: AssistantTurnRecord
) -> AssistantTurnRecord:
    semantic_existing = (
        existing.id,
        existing.session_id,
        existing.status,
        existing.content,
        existing.in_reply_to_interaction_id,
        existing.output,
        existing.idempotency_key,
        existing.command_fingerprint,
        existing.event_id,
    )
    semantic_requested = (
        requested.id,
        requested.session_id,
        requested.status,
        requested.content,
        requested.in_reply_to_interaction_id,
        requested.output,
        requested.idempotency_key,
        requested.command_fingerprint,
        requested.event_id,
    )
    if semantic_existing == semantic_requested:
        return existing
    raise IdempotencyConflictError("assistant turn retry identity has different content")


def _same_presentation_or_conflict(
    existing: TutorPresentationRecord, requested: TutorPresentationRecord
) -> TutorPresentationRecord:
    semantic_existing = (
        existing.id,
        existing.session_id,
        existing.kind,
        existing.content,
        existing.in_reply_to_interaction_id,
        existing.host_turn_id,
        existing.observed_host_context_sequence,
        existing.host_context_fingerprint,
        existing.decision_fingerprint,
        existing.receipt_fingerprint,
        existing.continuation_fingerprint,
        existing.capability_identity,
        existing.response_schema,
        existing.idempotency_key,
        existing.command_fingerprint,
        existing.event_id,
    )
    semantic_requested = (
        requested.id,
        requested.session_id,
        requested.kind,
        requested.content,
        requested.in_reply_to_interaction_id,
        requested.host_turn_id,
        requested.observed_host_context_sequence,
        requested.host_context_fingerprint,
        requested.decision_fingerprint,
        requested.receipt_fingerprint,
        requested.continuation_fingerprint,
        requested.capability_identity,
        requested.response_schema,
        requested.idempotency_key,
        requested.command_fingerprint,
        requested.event_id,
    )
    if semantic_existing == semantic_requested:
        return existing
    raise IdempotencyConflictError("tutor presentation retry identity has different content")


def _session_id(context: ExecutionContext) -> SessionId:
    if context.session_id is None:  # pragma: no cover - guarded by service
        raise SessionCommandError("session turn commands require context.session_id")
    return context.session_id


def _require_active(session: StudySessionRecord) -> None:
    if session.status is not SessionStatus.ACTIVE:
        raise SessionCommandError("session must be active")


def _expected_sequence(value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool):
        raise TypeError("expected_sequence must be an integer")
    if value < 0:
        raise ValueError("expected_sequence cannot be negative")
