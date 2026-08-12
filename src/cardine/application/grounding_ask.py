"""Canonical crash-safe grounded-question application use case."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from hashlib import sha256
from typing import Protocol

from cardine.courses import course_profile_manifest
from cardine.knowledge import SourcePin
from study_agent.domain import (
    AnswerId,
    Citation,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object, require_text
from study_agent.domain.session import (
    AnswerRecord,
    InteractionKind,
    SessionStatus,
    StudySessionRecord,
)
from study_agent.playbooks import (
    CancelledRunResult,
    EngineErrorCode,
    FailedRunResult,
    PlaybookEngine,
    PlaybookEngineError,
    PlaybookRunStatus,
    ReadDependency,
    SuspendedRunResult,
    ToolBehaviorPin,
    ToolExecutor,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import (
    CourseNotFoundError,
    CourseViewPort,
    EventStore,
    IndexReceipt,
    RetrievalPort,
    RunStore,
    SessionNotFoundError,
    SessionViewPort,
    SourceContentPort,
)
from study_agent.ports.retrieval import (
    RetrievalCatalogPort,
    RetrievalDocument,
    retrieval_catalog_fingerprint,
)
from study_agent.prompts import GROUNDED_ANSWER_PROMPT
from study_agent.sessions import (
    GroundedSessionFinalizer,
    IdempotencyConflictError,
    RetryableSessionConflictError,
    SessionCommandError,
    SessionService,
    summary_payload,
)
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import canonical_json_bytes
from study_agent.tools.playbook_bridge import grounding_playbook_tools


class GroundingAskErrorCode(StrEnum):
    INVALID_REQUEST = "invalid_request"
    UNAUTHORIZED = "unauthorized"
    NOT_FOUND = "not_found"
    CONFLICT = "conflict"
    RETRYABLE_CONFLICT = "retryable_conflict"
    RUNNING = "running"
    SUSPENDED = "suspended"
    FAILED = "failed"
    INCOMPATIBLE_RUNTIME = "incompatible_runtime"
    EXECUTION_FAILED = "execution_failed"


class GroundingAskError(RuntimeError):
    """Stable application error that never exposes provider or source details."""

    def __init__(self, code: GroundingAskErrorCode, message: str) -> None:
        self.code = code
        require_text(message, "grounding ask error message")
        super().__init__(message)


class GroundingStudyEventKind(StrEnum):
    ACCEPTED = "grounding.accepted"
    COMPLETED = "grounding.completed"
    SUSPENDED = "grounding.suspended"
    FAILED = "grounding.failed"


@dataclass(frozen=True, slots=True)
class GroundingStudyEvent:
    kind: GroundingStudyEventKind
    course_id: CourseId
    session_id: SessionId
    run_id: RunId
    answer_id: AnswerId | None = None

    def __post_init__(self) -> None:
        if (self.kind is GroundingStudyEventKind.COMPLETED) != (self.answer_id is not None):
            raise ValueError("only completed grounding events identify an answer")


@dataclass(frozen=True, slots=True)
class GroundingAskResult:
    answer: AnswerRecord
    events: tuple[GroundingStudyEvent, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "events", tuple(self.events))
        if tuple(event.kind for event in self.events) != (
            GroundingStudyEventKind.ACCEPTED,
            GroundingStudyEventKind.COMPLETED,
        ):
            raise ValueError("grounding result requires accepted then completed events")
        if any(event.run_id != self.answer.run_id for event in self.events):
            raise ValueError("grounding events must identify the answer run")
        if self.events[-1].answer_id != self.answer.id:
            raise ValueError("completed grounding event must identify the answer")


class GroundingEngineFactory(Protocol):
    """Composition-root hook that adds request-bound tools to a configured engine."""

    def create(self, *, tools: tuple[ToolExecutor, ...]) -> PlaybookEngine: ...


@dataclass(frozen=True, slots=True)
class GroundingAskConfiguration:
    pins: VersionPins
    index_receipt: IndexReceipt
    retrieval_limit: int = 8
    configuration_version: str = "grounding-ask-service@1"
    lesson_pin: SourcePin | None = None

    def __post_init__(self) -> None:
        require_text(self.configuration_version, "configuration_version")
        if self.retrieval_limit < 1:
            raise ValueError("retrieval_limit must be positive")
        if self.lesson_pin is not None and not isinstance(self.lesson_pin, SourcePin):
            raise TypeError("lesson_pin must be a SourcePin")
        _validate_builtin_pins(self.pins)


class GroundingAskService:
    """Own execution, verified recovery, and one canonical answer commit."""

    def __init__(
        self,
        *,
        courses: CourseViewPort,
        session_service: SessionService,
        sessions: SessionViewPort,
        retrieval: RetrievalPort,
        catalog: RetrievalCatalogPort,
        content: SourceContentPort,
        finalizer: GroundedSessionFinalizer,
        engine_factory: GroundingEngineFactory,
        run_store: RunStore,
        configuration: GroundingAskConfiguration,
        events: EventStore | None = None,
    ) -> None:
        self._courses = courses
        self._session_service = session_service
        self._sessions = sessions
        self._retrieval = retrieval
        self._catalog = catalog
        self._content = content
        self._finalizer = finalizer
        self._engine_factory = engine_factory
        self._run_store = run_store
        self._configuration = configuration
        self._events = events

    async def ask(
        self,
        question: str,
        context: ExecutionContext,
        *,
        expected_sequence: int | None = None,
    ) -> GroundingAskResult:
        question = _question(question)
        if expected_sequence is not None and (
            type(expected_sequence) is not int or expected_sequence < 0
        ):
            raise GroundingAskError(
                GroundingAskErrorCode.INVALID_REQUEST,
                "expected_sequence must be a non-negative integer",
            )
        session_id, key = self._authorize(context)
        try:
            profile = self._courses.get(context.course_id)
            session = self._sessions.get_session(context.course_id, session_id)
        except (CourseNotFoundError, SessionNotFoundError) as error:
            raise GroundingAskError(
                GroundingAskErrorCode.NOT_FOUND,
                "the requested course or session was not found",
            ) from error
        if session.course_id != context.course_id or session.id != session_id:
            raise GroundingAskError(
                GroundingAskErrorCode.UNAUTHORIZED,
                "the execution context does not own the session",
            )
        if session.status is not SessionStatus.ACTIVE:
            raise GroundingAskError(
                GroundingAskErrorCode.CONFLICT,
                "grounded questions require an active session",
            )

        if expected_sequence is not None:
            if self._events is None:
                raise GroundingAskError(
                    GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                    "canonical event state could not be verified",
                )
            try:
                current_sequence = _current_sequence(self._events, context.course_id)
            except (LookupError, OSError, RuntimeError, ValueError) as error:
                raise GroundingAskError(
                    GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                    "canonical event state could not be verified",
                ) from error
            if current_sequence != expected_sequence:
                raise GroundingAskError(
                    GroundingAskErrorCode.RETRYABLE_CONFLICT,
                    "canonical session state advanced; retry safely",
                )

        profile_json = freeze_object(course_profile_manifest(profile))
        try:
            summary = self._session_service.get_context(context)
            summary_json: JsonValue = (
                None if summary is None else summary_payload(summary)["summary"]
            )
            dependencies = self._read_dependencies(
                context.course_id,
                session,
                profile_json,
                summary_json,
            )
        except GroundingAskError:
            raise
        except (LookupError, OSError, SessionCommandError, ValueError) as error:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "canonical grounding dependencies could not be verified",
            ) from error
        run_id = self._run_id(
            context.course_id,
            session_id,
            key,
            _fingerprint({"question": question}),
            dependencies,
        )
        existing = self._existing_by_key(context.course_id, session_id, key)
        if existing is not None:
            if self._existing_question(context.course_id, session_id, existing) != question:
                raise GroundingAskError(
                    GroundingAskErrorCode.CONFLICT,
                    "idempotency key already names a different grounded question",
                )
            expected_pin = (
                None
                if self._configuration.lesson_pin is None
                else _pin_fingerprint(self._configuration.lesson_pin)
            )
            if self._stored_lesson_pin(existing.run_id) != expected_pin:
                raise GroundingAskError(
                    GroundingAskErrorCode.CONFLICT,
                    "request identity is bound to a different lesson pin or read set",
                )
            return _result(existing, context.course_id, session_id)
        inputs: JsonObject = {
            "course_id": str(context.course_id),
            "session_id": str(session_id),
            "question": question,
        }
        tools = grounding_playbook_tools(
            context=context,
            question=question,
            retrieval=self._retrieval,
            course_profile=profile_json,
            continuation_summary=summary_json,
            index_receipt=self._configuration.index_receipt,
            limit=self._configuration.retrieval_limit,
        )
        engine = self._engine_factory.create(tools=tools)

        state = self._stored_status(run_id)
        if state is None:
            await self._execute(engine, run_id, inputs, dependencies)
        elif state != "completed":
            self._raise_existing_state(state)

        try:
            answer = self._finalizer.finalize_grounded_run(
                context=context,
                engine=engine,
                run_id=run_id,
                definition=GROUNDED_ANSWER_FLOW,
                inputs=inputs,
                pins=self._configuration.pins,
                read_dependencies=dependencies,
                idempotency_key=key,
                expected_sequence=expected_sequence,
            )
        except IdempotencyConflictError as error:
            raise GroundingAskError(GroundingAskErrorCode.CONFLICT, "request conflicts") from error
        except RetryableSessionConflictError as error:
            raise GroundingAskError(
                GroundingAskErrorCode.RETRYABLE_CONFLICT,
                "canonical session state advanced; retry safely",
            ) from error
        except (PlaybookEngineError, SessionCommandError, ValueError) as error:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "the persisted run could not be verified and finalized",
            ) from error
        return _result(answer, context.course_id, session_id)

    def _authorize(self, context: ExecutionContext) -> tuple[SessionId, str]:
        if not isinstance(context.principal_kind, PrincipalKind):
            raise GroundingAskError(
                GroundingAskErrorCode.UNAUTHORIZED,
                "grounded questions require a trusted principal",
            )
        if "study:ask" not in context.requested_capabilities:
            raise GroundingAskError(
                GroundingAskErrorCode.UNAUTHORIZED,
                "the study:ask capability is required",
            )
        if context.session_id is None:
            raise GroundingAskError(
                GroundingAskErrorCode.INVALID_REQUEST,
                "grounded questions require a session",
            )
        if context.idempotency_key is None:
            raise GroundingAskError(
                GroundingAskErrorCode.INVALID_REQUEST,
                "grounded questions require an idempotency key",
            )
        return context.session_id, context.idempotency_key

    async def _execute(
        self,
        engine: PlaybookEngine,
        run_id: RunId,
        inputs: JsonObject,
        dependencies: tuple[ReadDependency, ...],
    ) -> None:
        try:
            result = await engine.execute(
                run_id=run_id,
                skill=GROUNDED_ANSWER_SKILL,
                definition=GROUNDED_ANSWER_FLOW,
                inputs=inputs,
                pins=self._configuration.pins,
                read_dependencies=dependencies,
            )
        except PlaybookEngineError as error:
            if error.failure.code is EngineErrorCode.DUPLICATE_RUN:
                state = self._stored_status(run_id)
                if state == "completed":
                    return
                self._raise_existing_state(state or "incompatible")
            if error.failure.code in {
                EngineErrorCode.INCOMPATIBLE_ENGINE,
                EngineErrorCode.INCOMPATIBLE_PINS,
                EngineErrorCode.UNSUPPORTED_CAPABILITY,
                EngineErrorCode.UNSUPPORTED_TOOL,
                EngineErrorCode.UNSUPPORTED_VALIDATOR,
                EngineErrorCode.UNSUPPORTED_FALLBACK,
            }:
                raise GroundingAskError(
                    GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                    "the configured runtime cannot execute the grounded-answer skill",
                ) from error
            raise GroundingAskError(
                GroundingAskErrorCode.EXECUTION_FAILED,
                "grounded-answer execution failed safely",
            ) from error
        if result.status in {PlaybookRunStatus.COMPLETED, PlaybookRunStatus.TERMINATED}:
            return
        if isinstance(result, SuspendedRunResult):
            raise GroundingAskError(
                GroundingAskErrorCode.SUSPENDED,
                "grounded-answer execution is suspended",
            )
        if isinstance(result, CancelledRunResult):
            raise GroundingAskError(
                GroundingAskErrorCode.FAILED,
                "grounded-answer execution was cancelled safely",
            )
        if isinstance(result, FailedRunResult):
            raise GroundingAskError(
                GroundingAskErrorCode.FAILED,
                "grounded-answer execution failed safely",
            )
        raise GroundingAskError(
            GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
            "grounded-answer execution returned an unsupported state",
        )

    def _stored_status(self, run_id: RunId) -> str | None:
        try:
            payload = self._run_store.load(run_id)
        except (KeyError, FileNotFoundError):
            return None
        except OSError as error:
            raise GroundingAskError(
                GroundingAskErrorCode.EXECUTION_FAILED,
                "the persisted run state could not be read",
            ) from error
        try:
            root = json.loads(payload)
            status = root["checkpoint"]["status"]
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError) as error:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "the persisted run state is not readable",
            ) from error
        if not isinstance(status, str):
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "the persisted run state is invalid",
            )
        return status

    def _stored_lesson_pin(self, run_id: RunId) -> str | None:
        try:
            payload = self._run_store.load(run_id)
        except (KeyError, FileNotFoundError) as error:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "the persisted run state is not readable",
            ) from error
        except OSError as error:
            raise GroundingAskError(
                GroundingAskErrorCode.EXECUTION_FAILED,
                "the persisted run state could not be read",
            ) from error
        try:
            root = json.loads(payload)
            raw_dependencies = root["checkpoint"]["read_dependencies"]
            if not isinstance(raw_dependencies, list):
                raise TypeError
            matches = tuple(
                item
                for item in raw_dependencies
                if isinstance(item, list)
                and len(item) == 3
                and item[0] == "lesson_pin"
            )
            if len(matches) > 1 or (matches and not isinstance(matches[0][2], str)):
                raise ValueError
        except (UnicodeDecodeError, json.JSONDecodeError, KeyError, TypeError, ValueError) as error:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "the persisted run state is not readable",
            ) from error
        return None if not matches else matches[0][2]

    def _raise_existing_state(self, state: str) -> None:
        mapping = {
            "running": (GroundingAskErrorCode.RUNNING, "grounded-answer execution is running"),
            "suspended": (
                GroundingAskErrorCode.SUSPENDED,
                "grounded-answer execution is suspended",
            ),
            "failed": (GroundingAskErrorCode.FAILED, "grounded-answer execution failed safely"),
        }
        code, message = mapping.get(
            state,
            (
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "the persisted run state is incompatible",
            ),
        )
        raise GroundingAskError(code, message)

    def _existing_by_key(
        self, course_id: CourseId, session_id: SessionId, key: str
    ) -> AnswerRecord | None:
        matches = tuple(
            answer
            for answer in self._sessions.answers(course_id, session_id)
            if answer.idempotency_key == key
        )
        if len(matches) > 1:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "canonical answers contain duplicate idempotency identities",
            )
        return matches[0] if matches else None

    def _existing_question(
        self, course_id: CourseId, session_id: SessionId, answer: AnswerRecord
    ) -> str:
        matches = tuple(
            interaction
            for interaction in self._sessions.interactions(course_id, session_id)
            if interaction.id == answer.question_interaction_id
        )
        if len(matches) != 1 or matches[0].kind is not InteractionKind.HUMAN:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "canonical answer question linkage is invalid",
            )
        return matches[0].content

    def _read_dependencies(
        self,
        course_id: CourseId,
        session: StudySessionRecord,
        profile: JsonObject,
        summary_json: JsonValue,
    ) -> tuple[ReadDependency, ...]:
        documents = tuple(self._catalog.documents(include_superseded=True))
        if self._configuration.index_receipt.indexed_chunks != len(documents):
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "retrieval index receipt does not match the canonical catalog",
            )
        source_version = _source_fingerprint(course_id, documents, self._content)
        if self._configuration.index_receipt.catalog_fingerprint != source_version:
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "retrieval index receipt does not commit to the canonical catalog",
            )
        index_version = _fingerprint(
            {
                "indexed_chunks": self._configuration.index_receipt.indexed_chunks,
                "index_version": self._configuration.index_receipt.index_version,
                "catalog_fingerprint": self._configuration.index_receipt.catalog_fingerprint,
            }
        )
        return (
            ReadDependency("course_profile", str(course_id), _fingerprint(profile)),
            ReadDependency("source_revision_set", str(course_id), source_version),
            ReadDependency("retrieval_index", str(course_id), index_version),
            ReadDependency(
                "session_state",
                str(session.id),
                _session_fingerprint(session, summary_json),
            ),
        ) + (() if self._configuration.lesson_pin is None else (
            ReadDependency(
                "lesson_pin",
                f"{self._configuration.lesson_pin.course_id}:"
                f"{self._configuration.lesson_pin.source_id}",
                _pin_fingerprint(self._configuration.lesson_pin),
            ),
        ))

    def _run_id(
        self,
        course_id: CourseId,
        session_id: SessionId,
        key: str,
        question_fingerprint: str,
        dependencies: tuple[ReadDependency, ...],
    ) -> RunId:
        payload: JsonObject = {
            "course_id": str(course_id),
            "session_id": str(session_id),
            "idempotency_key": key,
            "question_fingerprint": question_fingerprint,
            "pins": _pins_manifest(self._configuration.pins),
            "configuration_version": self._configuration.configuration_version,
            "retrieval_limit": self._configuration.retrieval_limit,
            "lesson_pin": (
                None
                if self._configuration.lesson_pin is None
                else _pin_manifest(self._configuration.lesson_pin)
            ),
            "read_dependencies": tuple(
                {
                    "kind": dependency.kind,
                    "id": dependency.id,
                    "version": dependency.version,
                }
                for dependency in dependencies
            ),
        }
        return RunId("run-sha256:" + _fingerprint(payload))


def _question(value: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise GroundingAskError(
            GroundingAskErrorCode.INVALID_REQUEST,
            "question must be non-empty trimmed text",
        )
    return value


def _validate_builtin_pins(pins: VersionPins) -> None:
    expected_tools = (
        ToolBehaviorPin("session.get_context", GROUNDED_ANSWER_FLOW.version),
        ToolBehaviorPin("source.search", GROUNDED_ANSWER_FLOW.version),
    )
    if (
        pins.skill.id != GROUNDED_ANSWER_SKILL.id
        or pins.skill.version != GROUNDED_ANSWER_SKILL.version
        or pins.playbook.id != GROUNDED_ANSWER_FLOW.id
        or pins.playbook.version != GROUNDED_ANSWER_FLOW.version
        or pins.prompt != GROUNDED_ANSWER_PROMPT
        or pins.tool_behaviors != expected_tools
    ):
        raise ValueError("configuration pins must identify the exact grounded_answer@1 behavior")


def _pins_manifest(pins: VersionPins) -> JsonObject:
    return {
        "skill": {"id": pins.skill.id, "version": str(pins.skill.version)},
        "playbook": {"id": pins.playbook.id, "version": str(pins.playbook.version)},
        "prompt": {"id": pins.prompt.id, "version": str(pins.prompt.version)},
        "tool_behaviors": tuple(
            {"name": item.tool_name, "version": str(item.version)}
            for item in pins.tool_behaviors
        ),
        "model_adapter": {
            "id": pins.model_adapter.id,
            "version": str(pins.model_adapter.version),
        },
        "state_contract": {
            "id": pins.state_contract.id,
            "version": str(pins.state_contract.version),
        },
    }


def _pin_manifest(pin: SourcePin) -> JsonObject:
    return {
        "course_id": pin.course_id,
        "source_id": pin.source_id,
        "revision_id": pin.revision_id,
        "section_title": pin.section_title,
        "start_offset": pin.start_offset,
        "end_offset": pin.end_offset,
        "content_sha256": pin.content_sha256,
        "catalog_fingerprint": pin.catalog_fingerprint,
    }


def _pin_fingerprint(pin: SourcePin) -> str:
    return _fingerprint(_pin_manifest(pin))


def _source_fingerprint(
    course_id: CourseId,
    documents: tuple[RetrievalDocument, ...],
    content: SourceContentPort,
) -> str:
    for document in documents:
        if document.course_id != course_id:
            raise GroundingAskError(
                GroundingAskErrorCode.UNAUTHORIZED,
                "retrieval catalog returned another course",
            )
        chunk = document.chunk
        resolved = content.resolve(
            Citation(
                document.source_id,
                document.revision_id,
                chunk.chunk_id,
                chunk.start_offset,
                chunk.end_offset,
                "canonical chunk",
                document.text,
            )
        )
        if (
            resolved.text != document.text
            or resolved.citation.source_id != document.source_id
            or resolved.citation.revision_id != document.revision_id
            or resolved.citation.chunk_id != chunk.chunk_id
            or resolved.citation.start_offset != chunk.start_offset
            or resolved.citation.end_offset != chunk.end_offset
        ):
            raise GroundingAskError(
                GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                "retrieval catalog does not match canonical source content",
            )
    return retrieval_catalog_fingerprint(documents)


def _fingerprint(value: JsonValue) -> str:
    return sha256(
        b"study-agent-grounding-ask-v1\0" + canonical_json_bytes({"value": value})
    ).hexdigest()


def _session_fingerprint(
    session: StudySessionRecord, summary_json: JsonValue
) -> str:
    return _fingerprint(
        {
            "id": str(session.id),
            "course_id": str(session.course_id),
            "status": session.status.value,
            "started_at": session.started_at.isoformat(),
            "suspended_at": (
                None if session.suspended_at is None else session.suspended_at.isoformat()
            ),
            "resumed_at": (
                None if session.resumed_at is None else session.resumed_at.isoformat()
            ),
            "ended_at": None if session.ended_at is None else session.ended_at.isoformat(),
            "interaction_ids": tuple(str(item) for item in session.interaction_ids),
            "run_ids": tuple(str(item) for item in session.run_ids),
            "continuation_summary": summary_json,
        }
    )


def _current_sequence(events: EventStore, course_id: CourseId) -> int:
    stream = tuple(events.read(course_id))
    if not stream:
        return 0
    expected = tuple(range(1, len(stream) + 1))
    actual = tuple(event.course_sequence for event in stream)
    if actual != expected or any(event.course_id != course_id for event in stream):
        raise ValueError("course event stream is not contiguous")
    return actual[-1]


def _result(
    answer: AnswerRecord, course_id: CourseId, session_id: SessionId
) -> GroundingAskResult:
    return GroundingAskResult(
        answer,
        (
            GroundingStudyEvent(
                GroundingStudyEventKind.ACCEPTED,
                course_id,
                session_id,
                answer.run_id,
            ),
            GroundingStudyEvent(
                GroundingStudyEventKind.COMPLETED,
                course_id,
                session_id,
                answer.run_id,
                answer.id,
            ),
        ),
    )


__all__ = [
    "GroundingAskConfiguration",
    "GroundingAskError",
    "GroundingAskErrorCode",
    "GroundingAskResult",
    "GroundingAskService",
    "GroundingEngineFactory",
    "GroundingStudyEvent",
    "GroundingStudyEventKind",
]
