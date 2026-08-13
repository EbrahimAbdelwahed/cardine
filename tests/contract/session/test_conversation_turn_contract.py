from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from cardine.courses import CourseService, ProjectionCourseView, register_course_events
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    AssistantTurnRecord,
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    InteractionKind,
    PrincipalKind,
    RunId,
    SessionId,
)
from study_agent.domain._validation import JsonObject
from study_agent.playbooks import (
    PlaybookDefinition,
    PlaybookEngine,
    PlaybookRunStatus,
    PlaybookStep,
    RuntimeRegistries,
    ToolBehaviorPin,
    ToolStep,
    ValidateStep,
    ValidationOutcome,
    ValidatorDisposition,
    VerifiedRunRecord,
    VersionPins,
)
from study_agent.ports import (
    CancellationToken,
    EventSequenceConflictError,
    ModelCapabilities,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from study_agent.sessions import (
    IdempotencyConflictError,
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    RetryableSessionConflictError,
    SessionCommandError,
    SessionService,
    SessionTurnService,
    register_session_events,
)
from study_agent.skills import (
    ArtifactReference,
    GroundingPolicy,
    JsonSchema,
    PromptLayer,
    PromptLayerKind,
    SemanticVersion,
    SkillPackage,
    StateWritePolicy,
    ToolRequirement,
    ValidatorDefinition,
    VersionRange,
)
from study_agent.state import EventRegistry, Projection

NOW = datetime(2026, 7, 14, 9, tzinfo=UTC)
COURSE = CourseId("course-conversation")
SESSION = SessionId("session-conversation")
V1 = SemanticVersion.parse("1.0.0")


class Clock:
    def now(self) -> datetime:
        return NOW


class MemoryRunStore:
    def __init__(self) -> None:
        self.data: dict[RunId, bytes] = {}

    def create(self, run_id: RunId, payload: bytes) -> bool:
        if run_id in self.data:
            return False
        self.data[run_id] = payload
        return True

    def compare_and_set(self, run_id: RunId, expected: bytes, replacement: bytes) -> bool:
        if self.data.get(run_id) != expected:
            return False
        self.data[run_id] = replacement
        return True

    def load(self, run_id: RunId) -> bytes:
        return self.data[run_id]


class UnusedModel:
    @property
    def capabilities(self) -> ModelCapabilities:
        return ModelCapabilities()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        raise AssertionError(f"unexpected model request: {request}")

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        raise AssertionError(f"unexpected model stream: {request}")

    async def cancel(self, token: CancellationToken) -> None:
        raise AssertionError(f"unexpected cancellation: {token}")


class TutorMessageTool:
    def __init__(self, output: object) -> None:
        self.output = cast(dict[str, object], output)

    @property
    def name(self) -> str:
        return "tutor.message"

    @property
    def behavior_version(self) -> SemanticVersion:
        return V1

    async def invoke(self, arguments):  # type: ignore[no-untyped-def]
        assert arguments == {}
        return self.output


class TerminationValidator:
    @property
    def id(self) -> str:
        return "tutor.termination"

    @property
    def version(self) -> SemanticVersion:
        return V1

    async def validate(self, inputs):  # type: ignore[no-untyped-def]
        assert "tutor_message" in inputs
        return ValidationOutcome(
            True, ValidatorDisposition.TERMINATE, {}, "safe termination"
        )


def _pins() -> VersionPins:
    return VersionPins(
        ArtifactReference("conversation_fixture", V1),
        ArtifactReference("conversation_flow", V1),
        ArtifactReference("conversation_prompt", V1),
        (ToolBehaviorPin("tutor.message", V1),),
        ArtifactReference("unused_model", V1),
        ArtifactReference("event_state", V1),
    )


def _definition(terminated: bool) -> PlaybookDefinition:
    steps: list[PlaybookStep] = [
        ToolStep(
            "compose",
            ArtifactReference("tutor.message", V1),
            {},
            "tutor_message",
        )
    ]
    if terminated:
        steps.append(
            ValidateStep(
                "terminate",
                ArtifactReference("tutor.termination", V1),
                ("tutor_message",),
                "termination",
            )
        )
    return PlaybookDefinition(
        "conversation_flow",
        V1,
        VersionRange(V1, SemanticVersion.parse("2.0.0")),
        tuple(steps),
        ("course_id", "session_id"),
    )


def _skill(definition: PlaybookDefinition, terminated: bool) -> SkillPackage:
    return SkillPackage(
        "conversation_fixture",
        V1,
        "Persist a tutor-message fixture through the real engine.",
        VersionRange(V1, SemanticVersion.parse("2.0.0")),
        JsonSchema({"type": "object"}),
        JsonSchema({"type": "object"}),
        (
            PromptLayer(
                "policy", V1, PromptLayerKind.STUDY_SECURITY_POLICY, "Test policy."
            ),
        ),
        (),
        GroundingPolicy(False, "insufficient_evidence"),
        StateWritePolicy(),
        (),
        (ToolRequirement("tutor.message", V1),),
        ArtifactReference(definition.id, definition.version),
        validators=(
            (ValidatorDefinition("tutor.termination", V1, "Terminate safely."),)
            if terminated
            else ()
        ),
    )


def _persisted_engine(run: VerifiedRunRecord) -> tuple[PlaybookEngine, PlaybookDefinition]:
    terminated = run.status is PlaybookRunStatus.TERMINATED
    definition = _definition(terminated)
    engine = PlaybookEngine(
        engine_version=V1,
        model_adapter=ArtifactReference("unused_model", V1),
        state_contract=ArtifactReference("event_state", V1),
        model=UnusedModel(),
        registries=RuntimeRegistries(
            (TutorMessageTool(run.outputs["tutor_message"]),),
            ((TerminationValidator(),) if terminated else ()),
        ),
        run_store=MemoryRunStore(),
        clock=Clock(),
    )
    result = asyncio.run(
        engine.execute(
            run_id=run.run_id,
            skill=_skill(definition, terminated),
            definition=definition,
            inputs=run.inputs,
            pins=_pins(),
        )
    )
    assert result.status is run.status
    return engine, definition


def _run(
    *,
    run_id: str = "run-1",
    status: PlaybookRunStatus = PlaybookRunStatus.COMPLETED,
    message_status: str | None = None,
    content: str = "Tell me what material you have available.",
    reply: str | None = None,
    course_id: CourseId = COURSE,
    session_id: SessionId = SESSION,
) -> VerifiedRunRecord:
    termination = (
        ValidationOutcome(True, ValidatorDisposition.TERMINATE, {}, "safe termination")
        if status is PlaybookRunStatus.TERMINATED
        else None
    )
    return VerifiedRunRecord(
        RunId(run_id),
        "definition-fingerprint",
        {"course_id": str(course_id), "session_id": str(session_id)},
        _pins(),
        (),
        {
            "tutor_message": {
                "schema_version": 1,
                "status": message_status or status.value,
                "content": content,
                "in_reply_to_interaction_id": reply,
            }
        },
        (),
        status,
        termination,
    )


def _context(
    key: str | None,
    *,
    actor: PrincipalKind = PrincipalKind.SERVICE,
    course_id: CourseId = COURSE,
    session_id: SessionId | None = SESSION,
) -> ExecutionContext:
    return ExecutionContext(
        actor,
        "conversation-test",
        course_id,
        CorrelationId(f"correlation-{key or 'setup'}"),
        session_id=session_id,
        idempotency_key=key,
    )


def _record(
    service: SessionTurnService,
    run: VerifiedRunRecord,
    key: str,
    expected_sequence: int,
) -> AssistantTurnRecord:
    engine, definition = _persisted_engine(run)
    return service.record_assistant_turn(
        context=_context(key),
        engine=engine,
        run_id=run.run_id,
        definition=definition,
        inputs=run.inputs,
        pins=run.pins,
        expected_sequence=expected_sequence,
    )


def _fixture(
    path: Path,
) -> tuple[
    SQLiteEventStore,
    ProjectionSessionView,
    ProjectionAssistantTurnView,
    SessionTurnService,
]:
    registry = EventRegistry()
    register_course_events(registry)
    register_session_events(registry)
    events = SQLiteEventStore(path, registry)
    courses = ProjectionCourseView(events.projection)
    CourseService(events, Clock(), courses).create(
        CourseProfile(
            COURSE,
            "Conversation course",
            "en",
            learning_goals=("Test adaptive tutoring",),
        ),
        _context(None, session_id=None),
    )
    sessions = ProjectionSessionView(events.projection)
    SessionService(events, Clock(), sessions, courses).start(_context(None))
    assistant_turns = ProjectionAssistantTurnView(events.projection)
    return (
        events,
        sessions,
        assistant_turns,
        SessionTurnService(events, Clock(), sessions, assistant_turns),
    )


def test_records_learner_human_event_and_verified_assistant_statuses(
    tmp_path: Path,
) -> None:
    events, sessions, turns, service = _fixture(tmp_path / "turns.sqlite3")
    learner = service.record_learner_turn("I have lecture notes.", _context("learner-1"), 2)
    assert learner.kind is InteractionKind.HUMAN
    assert sessions.get_context(COURSE, SESSION) is None

    completed = _record(service, _run(reply=str(learner.id)), "assistant-1", 3)
    terminated = _record(
        service,
        _run(
            run_id="run-2",
            status=PlaybookRunStatus.TERMINATED,
            content="I cannot safely continue with that request.",
            reply=str(learner.id),
        ),
        "assistant-2",
        4,
    )
    assert completed.status.value == "completed"
    assert terminated.status.value == "terminated"
    assert turns.turns(COURSE, SESSION) == (completed, terminated)
    assert events.verify_projection(COURSE)


@pytest.mark.parametrize(
    ("run", "match"),
    [
        (_run(message_status="terminated"), "disagrees"),
        (_run(message_status="cancelled"), "unsupported"),
        (_run(content=" untrimmed "), "content is invalid"),
    ],
)
def test_rejects_invalid_verified_output_without_mutation(
    tmp_path: Path, run: VerifiedRunRecord, match: str
) -> None:
    events, _, _, service = _fixture(tmp_path / f"invalid-{match}.sqlite3")
    before = tuple(events.read(COURSE))
    with pytest.raises(SessionCommandError, match=match):
        _record(service, run, "assistant-1", 2)
    assert tuple(events.read(COURSE)) == before


def test_authority_session_ownership_lifecycle_and_reply_are_fail_closed(
    tmp_path: Path,
) -> None:
    events, sessions, _, service = _fixture(tmp_path / "authority.sqlite3")
    engine, definition = _persisted_engine(_run())
    with pytest.raises(SessionCommandError, match="authority"):
        service.record_assistant_turn(
            context=_context("model", actor=PrincipalKind.MODEL),
            engine=engine,
            run_id=RunId("run-1"),
            definition=definition,
            inputs=_run().inputs,
            pins=_run().pins,
            expected_sequence=2,
        )
    with pytest.raises(SessionCommandError, match="authority"):
        service.record_assistant_turn(
            context=_context("human", actor=PrincipalKind.HUMAN),
            engine=engine,
            run_id=RunId("run-1"),
            definition=definition,
            inputs=_run().inputs,
            pins=_run().pins,
            expected_sequence=2,
        )
    with pytest.raises(SessionCommandError, match="idempotency"):
        service.record_learner_turn("Hello", _context(None), 2)
    with pytest.raises(LookupError):
        service.record_learner_turn(
            "Hello",
            _context(
                "orphan",
                course_id=CourseId("missing-course"),
                session_id=SessionId("missing-session"),
            ),
            0,
        )
    with pytest.raises(SessionCommandError, match="reply target"):
        _record(service, _run(reply="missing-interaction"), "bad-reply", 2)

    lifecycle = SessionService(
        events, Clock(), sessions, ProjectionCourseView(events.projection)
    )
    lifecycle.suspend(_context(None))
    with pytest.raises(SessionCommandError, match="active"):
        service.record_learner_turn("Too late", _context("late"), 3)


def test_exact_retry_precedes_stale_check_changed_retry_conflicts_and_stale_is_clean(
    tmp_path: Path,
) -> None:
    events, sessions, _, service = _fixture(tmp_path / "retry.sqlite3")
    first = service.record_learner_turn("Oral exam.", _context("learner-1"), 2)
    assert service.record_learner_turn(
        "Oral exam.", _context("learner-1"), 2
    ) == first
    with pytest.raises(IdempotencyConflictError, match="different content"):
        service.record_learner_turn("Written exam.", _context("learner-1"), 2)
    before = tuple(events.read(COURSE))
    with pytest.raises(RetryableSessionConflictError, match="advanced"):
        service.record_learner_turn("New request", _context("learner-2"), 2)
    assert tuple(events.read(COURSE)) == before

    assistant = _record(service, _run(), "assistant-1", 3)
    assert _record(service, _run(), "assistant-1", 3) == assistant
    with pytest.raises(IdempotencyConflictError, match="different content"):
        _record(service, _run(content="A different response."), "assistant-1", 3)

    lifecycle = SessionService(
        events, Clock(), sessions, ProjectionCourseView(events.projection)
    )
    lifecycle.suspend(_context(None))
    assert service.record_learner_turn("Oral exam.", _context("learner-1"), 2) == first
    assert _record(service, _run(), "assistant-1", 3) == assistant
    lifecycle.end(_context(None))
    assert service.record_learner_turn("Oral exam.", _context("learner-1"), 2) == first
    assert _record(service, _run(), "assistant-1", 3) == assistant
    with pytest.raises(SessionCommandError, match="active"):
        service.record_learner_turn("After end", _context("after-end"), 6)


def test_run_and_idempotency_are_unique_against_turns_and_grounded_answers(
    tmp_path: Path,
) -> None:
    events, sessions, turns, service = _fixture(tmp_path / "unique.sqlite3")
    _record(service, _run(), "assistant-1", 2)
    with pytest.raises(IdempotencyConflictError, match="different content"):
        _record(service, _run(run_id="run-1"), "assistant-2", 3)

    second = SessionId("session-conversation-2")
    lifecycle = SessionService(
        events, Clock(), sessions, ProjectionCourseView(events.projection)
    )
    lifecycle.start(_context(None, session_id=second))
    duplicate_run = _run(run_id="run-1", session_id=second)
    duplicate_engine, duplicate_definition = _persisted_engine(duplicate_run)
    with pytest.raises(IdempotencyConflictError, match="assistant turn"):
        service.record_assistant_turn(
            context=_context("second-key", session_id=second),
            engine=duplicate_engine,
            run_id=duplicate_run.run_id,
            definition=duplicate_definition,
            inputs=duplicate_run.inputs,
            pins=duplicate_run.pins,
            expected_sequence=4,
        )
    session_scoped = _run(run_id="run-2", session_id=second)
    scoped_engine, scoped_definition = _persisted_engine(session_scoped)
    recorded = service.record_assistant_turn(
        context=_context("assistant-1", session_id=second),
        engine=scoped_engine,
        run_id=session_scoped.run_id,
        definition=scoped_definition,
        inputs=session_scoped.inputs,
        pins=session_scoped.pins,
        expected_sequence=4,
    )
    assert recorded.session_id == second

    class AnswerOwningView:
        def __getattr__(self, name: str) -> object:
            return getattr(sessions, name)

        def answers(self, course_id: CourseId, session_id: SessionId) -> tuple[object, ...]:
            assert course_id == COURSE
            return (SimpleNamespace(run_id=RunId("grounded-run"), idempotency_key="grounded-key"),)

    guarded = SessionTurnService(
        events,
        Clock(),
        cast(ProjectionSessionView, AnswerOwningView()),
        turns,
    )
    with pytest.raises(IdempotencyConflictError, match="grounded answer"):
        _record(guarded, _run(run_id="grounded-run"), "new-key", 5)
    with pytest.raises(IdempotencyConflictError, match="grounded answer"):
        _record(guarded, _run(run_id="different-run"), "grounded-key", 5)


def test_append_race_is_retryable_and_does_not_forge_success(tmp_path: Path) -> None:
    events, sessions, turns, _ = _fixture(tmp_path / "race.sqlite3")

    class RacingEvents:
        def read(self, course_id: CourseId, after_sequence: int = 0):  # type: ignore[no-untyped-def]
            return events.read(course_id, after_sequence)

        def append(self, course_id: CourseId, expected_sequence: int, batch):  # type: ignore[no-untyped-def]
            raise EventSequenceConflictError(course_id, expected_sequence, expected_sequence + 1)

    service = SessionTurnService(cast(SQLiteEventStore, RacingEvents()), Clock(), sessions, turns)
    before = tuple(events.read(COURSE))
    with pytest.raises(RetryableSessionConflictError, match="raced"):
        _record(service, _run(), "assistant-race", 2)
    assert tuple(events.read(COURSE)) == before


def test_assistant_turn_view_rejects_malformed_entries_outside_requested_session() -> None:
    projection = Projection(
        COURSE,
        state=cast(
            JsonObject,
            {
                "sessions": {
                    str(SESSION): {
                        "session_id": str(SESSION),
                        "course_id": str(COURSE),
                    },
                    "other-session": {
                        "session_id": "other-session",
                        "course_id": str(COURSE),
                    },
                },
                "session_interactions": {},
                "session_assistant_turns": {"corrupt-other-turn": "not-an-object"},
            },
        ),
    )
    view = ProjectionAssistantTurnView(lambda course_id: projection)
    with pytest.raises(ValueError, match="projection entry is corrupt"):
        view.turns(COURSE, SESSION)


def test_assistant_turn_view_rejects_session_key_identity_mismatch() -> None:
    projection = Projection(
        COURSE,
        state=cast(
            JsonObject,
            {
                "sessions": {
                    str(SESSION): {
                        "session_id": "different-session",
                        "course_id": str(COURSE),
                    }
                },
                "session_interactions": {},
                "session_assistant_turns": {},
            },
        ),
    )
    view = ProjectionAssistantTurnView(lambda course_id: projection)
    with pytest.raises(ValueError, match="session projection ownership is corrupt"):
        view.turns(COURSE, SESSION)


@pytest.mark.parametrize("corruption", ["wrong-kind", "orphan", "cross-session"])
def test_assistant_turn_view_revalidates_reply_relationships(
    tmp_path: Path, corruption: str
) -> None:
    events, _, _, service = _fixture(tmp_path / f"corrupt-reply-{corruption}.sqlite3")
    learner = service.record_learner_turn("My exam is oral.", _context("learner"), 2)
    _record(service, _run(reply=str(learner.id)), "assistant", 3)
    projection = events.projection(COURSE)
    state = dict(projection.state)
    interactions = dict(cast(JsonObject, state["session_interactions"]))
    if corruption == "orphan":
        del interactions[str(learner.id)]
    else:
        reply = dict(cast(JsonObject, interactions[str(learner.id)]))
        reply["kind" if corruption == "wrong-kind" else "session_id"] = (
            "note" if corruption == "wrong-kind" else "other-session"
        )
        interactions[str(learner.id)] = reply
    state["session_interactions"] = interactions
    corrupt = Projection(COURSE, projection.sequence, cast(JsonObject, state))
    view = ProjectionAssistantTurnView(lambda course_id: corrupt)
    with pytest.raises(ValueError, match="reply linkage is corrupt"):
        view.turns(COURSE, SESSION)
