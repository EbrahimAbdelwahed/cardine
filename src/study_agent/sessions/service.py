"""Application services for canonical study-session mutations."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from hashlib import sha256

from study_agent.domain import (
    Actor,
    CourseId,
    DomainEvent,
    EventId,
    ExecutionContext,
    GroundedAnswer,
    InteractionId,
    PrincipalKind,
    RunId,
    SessionId,
    answer_id_for,
    assistant_interaction_id_for,
    question_interaction_id_for,
    session_event_id_for,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.session import (
    AnswerRecord,
    ContinuationSummaryV1,
    InteractionKind,
    InteractionRecord,
    SessionStatus,
    StudySessionRecord,
)
from study_agent.playbooks import (
    PlaybookDefinition,
    PlaybookEngine,
    ReadDependency,
)
from study_agent.playbooks import (
    VersionPins as PlaybookVersionPins,
)
from study_agent.ports import (
    ClockPort,
    CourseViewPort,
    EventSequenceConflictError,
    EventStore,
    SessionNotFoundError,
    SessionViewPort,
    SourceContentPort,
)
from study_agent.skills import StateWritePolicy
from study_agent.state import canonical_json_bytes

from .events import (
    SESSION_ANSWER_RECORDED,
    SESSION_CONTINUATION_SUMMARY_UPDATED,
    SESSION_ENDED,
    SESSION_INTERACTION_RECORDED,
    SESSION_RESUMED,
    SESSION_SCHEMA_VERSION,
    SESSION_STARTED,
    SESSION_SUSPENDED,
    answer_recorded_payload,
    grounded_answer_manifest,
    interaction_recorded_payload,
    lifecycle_payload,
    session_started_payload,
    summary_payload,
)
from .provenance import assemble_grounded_answer
from .summary import build_continuation_summary

_FINALIZER_EVENTS = (
    SESSION_INTERACTION_RECORDED,
    SESSION_ANSWER_RECORDED,
    SESSION_CONTINUATION_SUMMARY_UPDATED,
)


class SessionCommandError(ValueError):
    """A session command violates ownership or lifecycle rules."""


class IdempotencyConflictError(SessionCommandError):
    """A retry identity already names a different canonical command."""


class RetryableSessionConflictError(RuntimeError):
    """The stream raced and no byte-equivalent committed result was found."""


class StateWritePolicyError(SessionCommandError):
    """A finalizer event falls outside the skill's declared write policy."""


class SessionService:
    """Own lifecycle commands while exposing reads only through SessionViewPort."""

    def __init__(
        self,
        events: EventStore,
        clock: ClockPort,
        view: SessionViewPort,
        courses: CourseViewPort,
    ) -> None:
        self._events = events
        self._clock = clock
        self._view = view
        self._courses = courses

    def start(self, context: ExecutionContext) -> StudySessionRecord:
        self._courses.get(context.course_id)
        session_id = _context_session(context)
        try:
            return self._view.get_session(context.course_id, session_id)
        except SessionNotFoundError:
            pass
        sequence = _current_sequence(self._events, context.course_id)
        event = self._event(
            context,
            SESSION_STARTED,
            session_started_payload(session_id),
            sequence + 1,
            _command_event_id(context, SESSION_STARTED),
        )
        try:
            self._events.append(context.course_id, sequence, (event,))
        except EventSequenceConflictError:
            try:
                return self._view.get_session(context.course_id, session_id)
            except SessionNotFoundError as error:
                raise RetryableSessionConflictError("session start raced another write") from error
        return self._view.get_session(context.course_id, session_id)

    def record_note(self, context: ExecutionContext, content: str) -> InteractionRecord:
        sequence = _current_sequence(self._events, context.course_id)
        session = self._active(context)
        identity = context.idempotency_key or str(context.correlation_id)
        interaction_id = InteractionId(
            "interaction-sha256:"
            + _digest(context.course_id, session.id, identity, "note")
        )
        for existing in self._view.interactions(context.course_id, session.id):
            if existing.id == interaction_id:
                if existing.kind is InteractionKind.NOTE and existing.content == content:
                    return existing
                raise IdempotencyConflictError("note retry identity has different content")
        now = self._clock.now()
        note_event = self._event(
            context,
            SESSION_INTERACTION_RECORDED,
            interaction_recorded_payload(interaction_id, InteractionKind.NOTE, content),
            sequence + 1,
            _command_event_id(context, f"note:{identity}"),
            occurred_at=now,
        )
        current_interactions = self._view.interactions(context.course_id, session.id)
        current_answers = self._view.answers(context.course_id, session.id)
        note_record = InteractionRecord(
            interaction_id, InteractionKind.NOTE, now, content
        )
        summary = build_continuation_summary(
            (*current_interactions, note_record),
            {str(item.id): item for item in current_answers},
        )
        summary_event = self._event(
            context,
            SESSION_CONTINUATION_SUMMARY_UPDATED,
            summary_payload(summary),
            sequence + 2,
            _command_event_id(context, f"note-summary:{identity}"),
            occurred_at=now,
            causation_id=note_event.event_id,
        )
        try:
            self._events.append(
                context.course_id, sequence, (note_event, summary_event)
            )
        except EventSequenceConflictError as error:
            for existing in self._view.interactions(context.course_id, session.id):
                if existing.id == interaction_id:
                    if existing.kind is InteractionKind.NOTE and existing.content == content:
                        return existing
                    raise IdempotencyConflictError(
                        "note retry identity has different content"
                    ) from error
            raise RetryableSessionConflictError(
                "course stream advanced before the note batch committed"
            ) from error
        except ValueError as error:
            if "continuation summary" in str(error):
                raise RetryableSessionConflictError(
                    "canonical history changed while regenerating note context"
                ) from error
            raise
        return next(
            item
            for item in self._view.interactions(context.course_id, session.id)
            if item.id == interaction_id
        )

    def suspend(self, context: ExecutionContext) -> StudySessionRecord:
        return self._transition(context, SESSION_SUSPENDED, SessionStatus.ACTIVE)

    def resume(self, context: ExecutionContext) -> StudySessionRecord:
        return self._transition(context, SESSION_RESUMED, SessionStatus.SUSPENDED)

    def end(self, context: ExecutionContext) -> StudySessionRecord:
        session = self._owned(context)
        if session.status is SessionStatus.ENDED:
            return session
        return self._append_transition(context, SESSION_ENDED)

    def get_context(self, context: ExecutionContext) -> ContinuationSummaryV1 | None:
        session_id = _context_session(context)
        self._owned(context)
        return self._view.get_context(context.course_id, session_id)

    def _transition(
        self,
        context: ExecutionContext,
        event_type: str,
        expected: SessionStatus,
    ) -> StudySessionRecord:
        session = self._owned(context)
        target = {
            SESSION_SUSPENDED: SessionStatus.SUSPENDED,
            SESSION_RESUMED: SessionStatus.ACTIVE,
        }[event_type]
        if session.status is target:
            return session
        if session.status is not expected:
            raise SessionCommandError(
                f"{event_type} requires {expected.value} session status"
            )
        return self._append_transition(context, event_type)

    def _append_transition(
        self, context: ExecutionContext, event_type: str
    ) -> StudySessionRecord:
        sequence = _current_sequence(self._events, context.course_id)
        event = self._event(
            context,
            event_type,
            lifecycle_payload(),
            sequence + 1,
            _command_event_id(context, event_type),
        )
        self._events.append(context.course_id, sequence, (event,))
        return self._view.get_session(context.course_id, _context_session(context))

    def _owned(self, context: ExecutionContext) -> StudySessionRecord:
        _trusted_context(context)
        session = self._view.get_session(context.course_id, _context_session(context))
        if session.course_id != context.course_id or session.id != context.session_id:
            raise SessionCommandError("execution context does not own the session")
        return session

    def _active(self, context: ExecutionContext) -> StudySessionRecord:
        session = self._owned(context)
        if session.status is not SessionStatus.ACTIVE:
            raise SessionCommandError("session must be active")
        return session

    def _event(
        self,
        context: ExecutionContext,
        event_type: str,
        payload: JsonObject,
        sequence: int,
        event_id: EventId,
        *,
        occurred_at: datetime | None = None,
        causation_id: EventId | None = None,
    ) -> DomainEvent:
        _trusted_context(context)
        return DomainEvent(
            event_id,
            context.course_id,
            sequence,
            event_type,
            SESSION_SCHEMA_VERSION,
            Actor(context.principal_kind, context.principal_id),
            occurred_at or self._clock.now(),
            context.correlation_id,
            payload,
            _context_session(context),
            causation_id,
        )


class GroundedSessionFinalizer:
    """Commit one already-verified grounded run as one atomic event batch."""

    def __init__(
        self,
        events: EventStore,
        clock: ClockPort,
        view: SessionViewPort,
        content: SourceContentPort,
        state_write_policy: StateWritePolicy,
    ) -> None:
        self._events = events
        self._clock = clock
        self._view = view
        self._content = content
        self._policy = state_write_policy

    def finalize_grounded_run(
        self,
        *,
        context: ExecutionContext,
        engine: PlaybookEngine,
        run_id: RunId,
        definition: PlaybookDefinition,
        inputs: JsonObject,
        pins: PlaybookVersionPins,
        read_dependencies: tuple[ReadDependency, ...] = (),
        idempotency_key: str,
        expected_sequence: int | None = None,
    ) -> AnswerRecord:
        session_id = _context_session(context)
        _trusted_context(context)
        if expected_sequence is not None and (
            type(expected_sequence) is not int or expected_sequence < 0
        ):
            raise SessionCommandError("expected_sequence must be a non-negative integer")
        sequence = (
            _current_sequence(self._events, context.course_id)
            if expected_sequence is None
            else None
        )
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
        question = run.inputs.get("question")
        if not isinstance(question, str) or not question or question != question.strip():
            raise SessionCommandError("verified run question is invalid")
        session = self._view.get_session(context.course_id, session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise SessionCommandError("answers may be finalized only into active sessions")
        answer = assemble_grounded_answer(run, self._content)
        record = self._record(context, run.run_id, idempotency_key, question, answer)
        existing = self._existing(context.course_id, session_id, run.run_id, idempotency_key)
        if existing is not None:
            return _same_or_conflict(existing, record)

        if expected_sequence is not None:
            sequence = expected_sequence
        assert sequence is not None

        current_interactions = self._view.interactions(context.course_id, session_id)
        current_answers = self._view.answers(context.course_id, session_id)
        now = self._clock.now()
        question_record = InteractionRecord(
            record.question_interaction_id, InteractionKind.HUMAN, now, question
        )
        assistant_record = InteractionRecord(
            record.interaction_id,
            InteractionKind.ASSISTANT,
            now,
            _answer_content(record),
            record.id,
            record.run_id,
        )
        answer_map = {str(item.id): item for item in current_answers}
        answer_map[str(record.id)] = record
        summary = build_continuation_summary(
            (*current_interactions, question_record, assistant_record), answer_map
        )
        event_types = _FINALIZER_EVENTS
        if (
            len(self._policy.allowed_event_types) != len(event_types)
            or frozenset(self._policy.allowed_event_types) != frozenset(event_types)
        ):
            raise StateWritePolicyError(
                "grounded_answer state-write policy must exactly allow the finalizer batch"
            )
        payloads = (
            interaction_recorded_payload(
                record.question_interaction_id, InteractionKind.HUMAN, question
            ),
            answer_recorded_payload(record),
            summary_payload(summary),
        )
        ids = tuple(
            session_event_id_for(
                context.course_id, session_id, run.run_id, idempotency_key, event_type
            )
            for event_type in event_types
        )
        events = tuple(
            DomainEvent(
                event_id,
                context.course_id,
                sequence + offset,
                event_type,
                SESSION_SCHEMA_VERSION,
                Actor(context.principal_kind, context.principal_id),
                now,
                context.correlation_id,
                payload,
                session_id,
                ids[offset - 2] if offset > 1 else None,
            )
            for offset, (event_id, event_type, payload) in enumerate(
                zip(ids, event_types, payloads, strict=True), start=1
            )
        )
        try:
            self._events.append(context.course_id, sequence, events)
        except EventSequenceConflictError as error:
            raced = self._existing(
                context.course_id, session_id, run.run_id, idempotency_key
            )
            if raced is not None:
                return _same_or_conflict(raced, record)
            raise RetryableSessionConflictError(
                "course stream advanced before the answer batch committed"
            ) from error
        except ValueError as error:
            if "continuation summary" in str(error):
                raced = self._existing(
                    context.course_id, session_id, run.run_id, idempotency_key
                )
                if raced is not None:
                    return _same_or_conflict(raced, record)
                raise RetryableSessionConflictError(
                    "canonical history changed while finalizing the answer"
                ) from error
            raise
        return self._view.get_answer(context.course_id, session_id, record.id)

    def _record(
        self,
        context: ExecutionContext,
        run_id: RunId,
        key: str,
        question: str,
        answer: GroundedAnswer,
    ) -> AnswerRecord:
        answer_id = answer_id_for(context.course_id, _context_session(context), run_id, key)
        assistant_id = assistant_interaction_id_for(
            context.course_id, _context_session(context), run_id, key
        )
        question_id = question_interaction_id_for(
            context.course_id, _context_session(context), run_id, key
        )
        command: JsonObject = {
            "course_id": str(context.course_id),
            "session_id": str(_context_session(context)),
            "run_id": str(run_id),
            "idempotency_key": key,
            "question": question,
            "answer": grounded_answer_manifest(answer),
        }
        fingerprint = sha256(
            b"study-agent-session-finalize-v1\0" + canonical_json_bytes(command)
        ).hexdigest()
        return AnswerRecord(
            answer_id,
            assistant_id,
            question_id,
            run_id,
            key,
            fingerprint,
            answer,
        )

    def _existing(
        self,
        course_id: CourseId,
        session_id: SessionId,
        run_id: RunId,
        idempotency_key: str,
    ) -> AnswerRecord | None:
        candidates = tuple(
            item
            for item in self._view.answers(course_id, session_id)
            if item.run_id == run_id or item.idempotency_key == idempotency_key
        )
        if not candidates:
            return None
        if len(candidates) != 1:
            raise IdempotencyConflictError("run and idempotency identities disagree")
        return candidates[0]


def _same_or_conflict(existing: AnswerRecord, requested: AnswerRecord) -> AnswerRecord:
    if existing == requested and existing.command_fingerprint == requested.command_fingerprint:
        return existing
    raise IdempotencyConflictError("retry identity already committed different content")


def _answer_content(record: AnswerRecord) -> str:
    texts = tuple(segment.text for segment in record.answer.segments)
    if texts:
        return "\n\n".join(texts)
    note = record.answer.unsupported_information_note
    if note is None:
        raise SessionCommandError("validated answer has no assistant content")
    return note


def _context_session(context: ExecutionContext) -> SessionId:
    if context.session_id is None:
        raise SessionCommandError("session commands require context.session_id")
    return context.session_id


def _trusted_context(context: ExecutionContext) -> None:
    _context_session(context)
    if not isinstance(context.principal_kind, PrincipalKind):
        raise SessionCommandError("session commands require a trusted principal")


def _current_sequence(events: EventStore, course_id: CourseId) -> int:
    stream: Sequence[DomainEvent] = events.read(course_id)
    if not stream:
        return 0
    expected = tuple(range(1, len(stream) + 1))
    actual = tuple(event.course_sequence for event in stream)
    if actual != expected or any(event.course_id != course_id for event in stream):
        raise SessionCommandError("course event stream is not contiguous")
    return actual[-1]


def _command_event_id(context: ExecutionContext, purpose: str) -> EventId:
    return EventId(
        "event-sha256:"
        + _digest(
            context.course_id,
            _context_session(context),
            str(context.correlation_id),
            purpose,
        )
    )


def _digest(course_id: CourseId, session_id: SessionId, identity: str, purpose: str) -> str:
    return sha256(
        f"{course_id}\0{session_id}\0{identity}\0{purpose}".encode()
    ).hexdigest()
