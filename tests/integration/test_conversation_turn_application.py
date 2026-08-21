from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import pytest

from cardine.cli import EMPTY_CONFIG, LocalRepository, initialize_local_repository
from cardine.hosts import (
    PendingContinuationDescriptor,
    TutorContinuationRecord,
    TutorHostRunner,
    TutorHostRunResult,
    TutorHostRunStatus,
    TutorPresentationReceipt,
)
from study_agent.application import (
    ConversationTurnApplication,
    ConversationTurnCommand,
    ConversationTurnError,
    ConversationTurnErrorCode,
)
from study_agent.capabilities import CapabilityContinuation, TutorCapabilityId
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    RunId,
    SessionId,
    TutorPresentationKind,
)
from study_agent.playbooks import ToolBehaviorPin, VersionPins
from study_agent.ports import TutorContinuationStore, TutorSnapshotPort
from study_agent.skills import ArtifactReference, SemanticVersion

COURSE = CourseId("conversation-course")
SESSION = SessionId("conversation-session")
SHA = "a" * 64


@dataclass
class _Runner:
    snapshots: TutorSnapshotPort
    continuation_store: TutorContinuationStore
    mode: str = "message"
    completion_handoff_store: object | None = None

    def __post_init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self._descriptor: PendingContinuationDescriptor | None = None

    async def run(
        self,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
        interruption: object,
        *,
        pending_fingerprint: str | None = None,
    ) -> TutorHostRunResult:
        del interruption
        self.calls.append((host_turn_id, pending_fingerprint))
        if self.mode == "raise":
            raise RuntimeError("private runner detail")
        if self.mode == "message_without_receipt":
            return TutorHostRunResult(
                TutorHostRunStatus.ASSISTANT_MESSAGE,
                learner_text="uncommitted host text",
            )
        typed_failure_reasons = {
            "authentication",
            "endpoint_incompatible",
            "model_unavailable",
            "protocol_error",
            "rate_limited",
            "timeout",
            "unavailable",
        }
        if self.mode in {
            "failed",
            "interrupted",
            "budget",
            "budget_timeout",
            "in_progress",
            *typed_failure_reasons,
        }:
            status = {
                "failed": TutorHostRunStatus.FAILED,
                "interrupted": TutorHostRunStatus.INTERRUPTED,
                "budget": TutorHostRunStatus.BUDGET_EXHAUSTED,
                "budget_timeout": TutorHostRunStatus.BUDGET_EXHAUSTED,
                "in_progress": TutorHostRunStatus.IN_PROGRESS,
                **dict.fromkeys(typed_failure_reasons, TutorHostRunStatus.FAILED),
            }[self.mode]
            return TutorHostRunResult(
                status,
                failure_reason=(
                    "timeout"
                    if self.mode == "budget_timeout"
                    else self.mode
                    if self.mode in typed_failure_reasons
                    else None
                ),
            )
        if self.mode in {"completed", "terminated"}:
            return TutorHostRunResult(
                TutorHostRunStatus.COMPLETED
                if self.mode == "completed"
                else TutorHostRunStatus.TERMINATED
            )
        sequence = self.snapshots.get(course_id, session_id).high_water_sequence
        if self.mode == "suspend" and pending_fingerprint is None:
            descriptor = _descriptor()
            self._descriptor = descriptor
            self.continuation_store.create(
                course_id,
                session_id,
                descriptor.fingerprint,
                _continuation_record(course_id, session_id, descriptor).to_bytes(),
            )
            receipt = _receipt(
                host_turn_id,
                TutorPresentationKind.CONTINUATION_REQUEST,
                descriptor.dialogue_request,
                sequence,
                descriptor,
            )
            return TutorHostRunResult(
                TutorHostRunStatus.SUSPENDED,
                pending_continuation=descriptor,
                presentation_receipt=receipt,
            )
        kind = (
            TutorPresentationKind.LEARNER_QUESTION
            if self.mode == "question"
            else TutorPresentationKind.ASSISTANT_MESSAGE
        )
        content = (
            "What structure should we compare?"
            if kind is TutorPresentationKind.LEARNER_QUESTION
            else "Tutor response"
        )
        status = (
            TutorHostRunStatus.NEEDS_LEARNER_INPUT
            if kind is TutorPresentationKind.LEARNER_QUESTION
            else TutorHostRunStatus.ASSISTANT_MESSAGE
        )
        return TutorHostRunResult(
            status,
            learner_text=content,
            presentation_receipt=_receipt(host_turn_id, kind, content, sequence),
        )


def _descriptor() -> PendingContinuationDescriptor:
    continuation = _continuation()
    return PendingContinuationDescriptor(
        continuation.fingerprint,
        "explain_concept@1",
        "clarify",
        "Please clarify the target.",
        {
            "type": "object",
            "properties": {"answer": {"type": "boolean"}},
            "required": ("answer",),
            "additionalProperties": False,
        },
    )


def _continuation() -> CapabilityContinuation:
    version = SemanticVersion.parse("1.0.0")
    pins = VersionPins(
        ArtifactReference("skill", version),
        ArtifactReference("playbook", version),
        ArtifactReference("prompt", version),
        (ToolBehaviorPin("tool", version),),
        ArtifactReference("model", version),
        ArtifactReference("state", version),
    )
    return CapabilityContinuation(
        RunId("conversation-run"),
        TutorCapabilityId.EXPLAIN_CONCEPT,
        version,
        SHA,
        "b" * 64,
        "c" * 64,
        "d" * 64,
        "e" * 64,
        "clarify",
        1,
        {"topic": "valves"},
        pins,
        (),
    )


def _continuation_record(
    course_id: CourseId, session_id: SessionId, descriptor: PendingContinuationDescriptor
) -> TutorContinuationRecord:
    return TutorContinuationRecord(
        _continuation(),
        ExecutionContext(
            PrincipalKind.SERVICE,
            "host",
            course_id,
            CorrelationId("host-continuation"),
            session_id=session_id,
            idempotency_key="host-action",
        ),
        descriptor,
    )


def _receipt(
    host_turn_id: str,
    kind: TutorPresentationKind,
    content: str,
    sequence: int,
    descriptor: PendingContinuationDescriptor | None = None,
) -> TutorPresentationReceipt:
    return TutorPresentationReceipt(
        host_turn_id,
        kind,
        content,
        sequence,
        SHA,
        "b" * 64,
        None if descriptor is None else descriptor.fingerprint,
        None if descriptor is None else descriptor.capability_identity,
        None if descriptor is None else descriptor.response_schema,
    )


def _open(tmp_path: Path, runner_mode: str = "message") -> tuple[LocalRepository, _Runner, Path]:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    repository = LocalRepository.open(root)
    repository.course_service.create(
        CourseProfile(COURSE, "Conversation fixture", "en", learning_goals=("Learn",)),
        ExecutionContext(PrincipalKind.SERVICE, "course", COURSE, CorrelationId("course")),
    )
    repository.session_service.start(
        ExecutionContext(
            PrincipalKind.HUMAN,
            "learner",
            COURSE,
            CorrelationId("session"),
            session_id=SESSION,
        )
    )
    runner = _Runner(repository.tutor_snapshots, repository.tutor_continuations, runner_mode)
    repository.conversation = repository.conversation_application(
        cast(TutorHostRunner, runner), continuation_store=repository.tutor_continuations
    )
    return repository, runner, root


def _conversation(repository: LocalRepository) -> ConversationTurnApplication:
    conversation = repository.conversation
    assert conversation is not None
    return conversation


def _compose(repository: LocalRepository, runner: _Runner) -> ConversationTurnApplication:
    return repository.conversation_application(
        cast(TutorHostRunner, runner), continuation_store=repository.tutor_continuations
    )


def _command(
    request_id: str, sequence: int, content: str, session_id: SessionId = SESSION
) -> ConversationTurnCommand:
    return ConversationTurnCommand(
        content,
        ExecutionContext(
            PrincipalKind.HUMAN,
            "learner",
            COURSE,
            CorrelationId(f"request-{request_id}"),
            session_id=session_id,
            idempotency_key=request_id,
        ),
        sequence,
    )


def test_direct_message_restart_and_exact_retry_do_not_repeat_host_or_event(tmp_path: Path) -> None:
    repository, runner, root = _open(tmp_path)
    try:
        initial = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        command = _command("request-1", initial, "Hello tutor")
        first = asyncio.run(_conversation(repository).turn(command))
        assert first.presentation is not None
        assert first.presentation.kind is TutorPresentationKind.ASSISTANT_MESSAGE
        event_count = len(repository.events.read(COURSE))
        with LocalRepository.open(root) as reopened:
            retry_runner = _Runner(reopened.tutor_snapshots, reopened.tutor_continuations)
            reopened.conversation = _compose(reopened, retry_runner)
            retry = asyncio.run(_conversation(reopened).turn(command))
            assert retry.presentation == first.presentation
            assert len(runner.calls) == 1
            assert retry_runner.calls == []
            assert len(reopened.events.read(COURSE)) == event_count
    finally:
        repository.close()


@pytest.mark.parametrize(
    ("mode", "expected_status"),
    (("terminated", TutorHostRunStatus.TERMINATED),),
)
def test_status_only_terminal_retry_survives_restart_without_repeating_host(
    tmp_path: Path,
    mode: str,
    expected_status: TutorHostRunStatus,
) -> None:
    repository, runner, root = _open(tmp_path, mode)
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        command = _command(f"terminal-{mode}", sequence, "Bounded terminal turn")
        first = asyncio.run(_conversation(repository).turn(command))
        event_count = len(repository.events.read(COURSE))
        assert first.status is expected_status
        assert first.presentation is not None
        assert first.presentation.kind is TutorPresentationKind.ASSISTANT_MESSAGE
        assert "evidenze sufficienti" in first.presentation.content
        assert len(runner.calls) == 1

        with LocalRepository.open(root) as reopened:
            retry_runner = _Runner(reopened.tutor_snapshots, reopened.tutor_continuations, mode)
            reopened.conversation = _compose(reopened, retry_runner)
            retry = asyncio.run(_conversation(reopened).turn(command))
            assert retry.status is expected_status
            assert retry.presentation == first.presentation
            assert retry_runner.calls == []
            assert len(reopened.events.read(COURSE)) == event_count
    finally:
        repository.close()


def test_completed_without_recoverable_output_gets_a_visible_fallback(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path, "completed")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        result = asyncio.run(
            _conversation(repository).turn(
                _command("terminal-without-presentation", sequence, "Hello")
            )
        )
        assert result.status is TutorHostRunStatus.COMPLETED
        assert result.presentation is not None
        assert result.presentation.kind is TutorPresentationKind.ASSISTANT_MESSAGE
        assert "completato" in result.presentation.content
        assert "non è riuscito a pubblicarne il risultato" in result.presentation.content
        assert repository.tutor_presentations.presentations(COURSE, SESSION) == (result.presentation,)
        assert runner.calls
    finally:
        repository.close()


def test_repository_composition_requires_explicit_continuation_store(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        with pytest.raises(TypeError, match="continuation_store"):
            cast(Callable[..., object], repository.conversation_application)(cast(TutorHostRunner, runner))
    finally:
        repository.close()


def test_repository_composition_rejects_a_different_continuation_store_object(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        with pytest.raises(TypeError, match="exact continuation store"):
            repository.conversation_application(
                cast(TutorHostRunner, runner),
                continuation_store=cast(TutorContinuationStore, object()),
            )
    finally:
        repository.close()


def test_repository_composition_rejects_a_runner_with_a_different_handoff_store(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        runner.completion_handoff_store = object()
        with pytest.raises(TypeError, match="exact completion handoff store"):
            _compose(repository, runner)
    finally:
        repository.close()


def test_retry_of_resolved_resume_survives_lost_response_and_deleted_store_record(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path, "suspend")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        first = asyncio.run(_conversation(repository).turn(_command("request-1", sequence, "Start")))
        assert first.pending_continuation is not None
        fingerprint = first.pending_continuation.fingerprint
        resume_sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        resume_command = _command("request-2", resume_sequence, "Answer")
        resumed = asyncio.run(_conversation(repository).resume(fingerprint, resume_command))
        assert resumed.presentation is not None
        assert len(runner.calls) == 2
        assert runner.calls[0][1] is None
        assert runner.calls[1][1] == fingerprint

        retry = asyncio.run(_conversation(repository).resume(fingerprint, resume_command))
        assert retry.presentation == resumed.presentation
        assert retry.presentations == resumed.presentations
        assert len(runner.calls) == 2
    finally:
        repository.close()


def test_retry_returns_command_bound_presentation_when_later_turn_exists(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        first_sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        first_command = _command("request-1", first_sequence, "First")
        first = asyncio.run(_conversation(repository).turn(first_command))
        second_sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        second = asyncio.run(_conversation(repository).turn(_command("request-2", second_sequence, "Second")))

        retry = asyncio.run(_conversation(repository).turn(first_command))
        assert len(runner.calls) == 2
        assert len(retry.presentations) == 2
        assert retry.presentation == first.presentation
        assert retry.presentation != second.presentation
    finally:
        repository.close()


def test_same_request_changed_content_conflicts_and_stale_new_request_skips_host(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        asyncio.run(_conversation(repository).turn(_command("request-1", sequence, "Original")))
        with pytest.raises(ConversationTurnError) as changed:
            asyncio.run(_conversation(repository).turn(_command("request-1", sequence, "Changed")))
        assert changed.value.code is ConversationTurnErrorCode.CONFLICT
        calls = len(runner.calls)
        with pytest.raises(ConversationTurnError) as stale:
            asyncio.run(_conversation(repository).turn(_command("request-2", sequence, "Stale")))
        assert stale.value.code is ConversationTurnErrorCode.RETRYABLE_CONFLICT
        assert len(runner.calls) == calls
    finally:
        repository.close()


def test_learner_question_is_a_canonical_presentation(tmp_path: Path) -> None:
    repository, _runner, _ = _open(tmp_path, "question")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        result = asyncio.run(_conversation(repository).turn(_command("question-1", sequence, "Explain")))
        assert result.status is TutorHostRunStatus.NEEDS_LEARNER_INPUT
        assert result.presentation is not None
        assert result.presentation.kind is TutorPresentationKind.LEARNER_QUESTION
        assert result.presentation.content == "What structure should we compare?"
        persisted = repository.tutor_presentations.presentations(COURSE, SESSION)
        assert len(persisted) == 1
        assert persisted[0].kind is TutorPresentationKind.LEARNER_QUESTION
        assert persisted[0].content == "What structure should we compare?"
    finally:
        repository.close()


def test_suspend_restart_resume_and_resolved_continuation_is_inactive(tmp_path: Path) -> None:
    repository, _runner, root = _open(tmp_path, "suspend")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        first = asyncio.run(_conversation(repository).turn(_command("request-1", sequence, "Start")))
        assert first.pending_continuation is not None
        fingerprint = first.pending_continuation.fingerprint
        with LocalRepository.open(root) as reopened:
            resumed_runner = _Runner(reopened.tutor_snapshots, reopened.tutor_continuations, "message")
            reopened.conversation = _compose(reopened, resumed_runner)
            sequence = reopened.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
            resumed = asyncio.run(
                _conversation(reopened).resume(fingerprint, _command("request-2", sequence, "Answer"))
            )
            assert resumed.presentation is not None
            assert resumed.pending_continuation is None
            with pytest.raises(KeyError):
                reopened.tutor_continuations.load(COURSE, SESSION, fingerprint)
    finally:
        repository.close()


def test_missing_operational_continuation_is_degraded_and_unresumable(tmp_path: Path) -> None:
    repository, _runner, _ = _open(tmp_path, "suspend")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        first = asyncio.run(_conversation(repository).turn(_command("request-1", sequence, "Start")))
        assert first.pending_continuation is not None
        fingerprint = first.pending_continuation.fingerprint
        repository.tutor_continuations.delete(COURSE, SESSION, fingerprint)
        assert (
            asyncio.run(_conversation(repository).turn(_command("request-1", sequence, "Start"))).pending_continuation
            is None
        )
        interactions_before_resume = repository.sessions.interactions(COURSE, SESSION)
        with pytest.raises(ConversationTurnError) as missing:
            asyncio.run(
                _conversation(repository).resume(
                    fingerprint, _command("request-2", sequence + 1, "Answer")
                )
            )
        assert missing.value.code is ConversationTurnErrorCode.CONFLICT
        assert repository.sessions.interactions(COURSE, SESSION) == interactions_before_resume
    finally:
        repository.close()


def test_concurrent_new_requests_at_one_sequence_have_one_canonical_winner(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence

        async def submit(request_id: str) -> object:
            try:
                return await _conversation(repository).turn(_command(request_id, sequence, request_id))
            except ConversationTurnError as error:
                return error

        async def race() -> tuple[object, object]:
            return await asyncio.gather(submit("race-a"), submit("race-b"))

        first, second = asyncio.run(race())
        outcomes = (first, second)
        assert sum(isinstance(item, ConversationTurnError) is False for item in outcomes) == 1
        errors = tuple(item for item in outcomes if isinstance(item, ConversationTurnError))
        assert errors and errors[0].code is ConversationTurnErrorCode.RETRYABLE_CONFLICT
        assert len(runner.calls) == 1
        assert len(repository.tutor_presentations.presentations(COURSE, SESSION)) == 1
    finally:
        repository.close()


@pytest.mark.parametrize(
    "mode,expected_status,expected_text",
    [
        ("failed", TutorHostRunStatus.FAILED, "Non sono riuscito"),
        ("interrupted", TutorHostRunStatus.INTERRUPTED, "Non sono riuscito"),
        ("budget", TutorHostRunStatus.BUDGET_EXHAUSTED, "Non sono riuscito"),
        ("raise", TutorHostRunStatus.FAILED, "Non sono riuscito"),
        ("message_without_receipt", TutorHostRunStatus.FAILED, "Non sono riuscito"),
    ],
)
def test_unsuccessful_host_results_commit_a_visible_fallback(
    tmp_path: Path,
    mode: str,
    expected_status: TutorHostRunStatus,
    expected_text: str,
) -> None:
    repository, runner, _ = _open(tmp_path, mode)
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        result = asyncio.run(
            _conversation(repository).turn(_command(f"request-{mode}", sequence, "Hello"))
        )
        assert result.status is expected_status
        assert result.presentation is not None
        assert result.presentation.kind is TutorPresentationKind.ASSISTANT_MESSAGE
        assert expected_text in result.presentation.content
        assert repository.tutor_presentations.presentations(COURSE, SESSION) == (result.presentation,)
        assert runner.calls
    finally:
        repository.close()


def test_in_progress_remains_retryable_without_settling_the_turn(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path, "in_progress")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        command = _command("request-in-progress", sequence, "Hello")
        for _attempt in range(2):
            with pytest.raises(ConversationTurnError) as error:
                asyncio.run(_conversation(repository).turn(command))
            assert error.value.code is ConversationTurnErrorCode.FAILED
            assert error.value.learner_persisted is True
        assert len(runner.calls) == 2
        assert repository.tutor_presentations.presentations(COURSE, SESSION) == ()
    finally:
        repository.close()


@pytest.mark.parametrize(
    "failure_reason",
    ("rate_limited", "timeout", "unavailable", "model_unavailable"),
)
def test_transient_provider_failure_retries_without_fallback_or_duplicate_learner(
    tmp_path: Path,
    failure_reason: str,
) -> None:
    repository, runner, _ = _open(tmp_path, failure_reason)
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        command = _command(f"request-{failure_reason}", sequence, "Retry this tutor turn")
        for _attempt in range(2):
            with pytest.raises(ConversationTurnError) as error:
                asyncio.run(_conversation(repository).turn(command))
            assert error.value.code is ConversationTurnErrorCode.FAILED
            assert error.value.failure_reason == failure_reason
            assert error.value.learner_persisted is True
        assert len(runner.calls) == 2
        assert repository.tutor_presentations.presentations(COURSE, SESSION) == ()
        learner_rows = tuple(
            item
            for item in repository.tutor_snapshots.get(COURSE, SESSION).timeline
            if item.kind.value == "learner"
        )
        assert len(learner_rows) == 1
    finally:
        repository.close()


def test_budget_exhaustion_preserves_transient_failure_for_exact_retry(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path, "budget_timeout")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        command = _command("request-budget-timeout", sequence, "Retry this tutor turn")
        for _attempt in range(2):
            with pytest.raises(ConversationTurnError) as error:
                asyncio.run(_conversation(repository).turn(command))
            assert error.value.code is ConversationTurnErrorCode.FAILED
            assert error.value.failure_reason == "timeout"
            assert error.value.learner_persisted is True
        assert len(runner.calls) == 2
        assert repository.tutor_presentations.presentations(COURSE, SESSION) == ()
        learner_rows = tuple(
            item
            for item in repository.tutor_snapshots.get(COURSE, SESSION).timeline
            if item.kind.value == "learner"
        )
        assert len(learner_rows) == 1
    finally:
        repository.close()


@pytest.mark.parametrize(
    "failure_reason",
    ("authentication", "endpoint_incompatible", "protocol_error", None),
)
def test_non_transient_provider_failure_commits_safe_fallback(
    tmp_path: Path,
    failure_reason: str | None,
) -> None:
    repository, runner, _ = _open(tmp_path, failure_reason or "failed")
    try:
        sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        command = _command(f"request-{failure_reason or 'reasonless'}", sequence, "Save this turn")
        result = asyncio.run(_conversation(repository).turn(command))

        assert result.status is TutorHostRunStatus.FAILED
        if failure_reason in {"authentication", "endpoint_incompatible"}:
            assert "modello non è disponibile" in result.presentation.content
            assert "Impostazioni" in result.presentation.content
        else:
            assert "Non sono riuscito" in result.presentation.content
        assert repository.tutor_presentations.presentations(COURSE, SESSION) == (result.presentation,)
        assert len(runner.calls) == 1
    finally:
        repository.close()


def test_request_identity_is_scoped_to_session(tmp_path: Path) -> None:
    repository, runner, _ = _open(tmp_path)
    try:
        repository.session_service.start(
            ExecutionContext(
                PrincipalKind.HUMAN,
                "learner",
                COURSE,
                CorrelationId("session-2"),
                session_id=SessionId("session-2"),
            )
        )
        first_seq = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
        asyncio.run(_conversation(repository).turn(_command("same-request", first_seq, "One")))
        second_seq = repository.tutor_snapshots.get(COURSE, SessionId("session-2")).high_water_sequence
        asyncio.run(
            _conversation(repository).turn(
                _command("same-request", second_seq, "Two", SessionId("session-2"))
            )
        )
        assert len(runner.calls) == 2
    finally:
        repository.close()
