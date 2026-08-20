from __future__ import annotations

import asyncio
from pathlib import Path
from types import MethodType
from typing import Protocol, cast

from cardine.application.conversation_turn import ConversationTurnCommand
from cardine.cli.repository import LocalRepository
from cardine.hosts import TutorHostRunStatus
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SessionId
from study_agent.ports import ModelFinishReason, ModelInvocation, ModelRequest, ModelResponse
from tests.integration.demo.TUT08.test_repository_backed_chat import _repository

COURSE = CourseId("cardine-course")
SESSION = SessionId("cardine-session")


class _ModelWithGenerate(Protocol):
    async def generate(self, request: ModelRequest) -> ModelResponse: ...


def _install_recovery_model(model: object, queries: tuple[str, ...]) -> list[ModelRequest]:
    typed_model = cast(_ModelWithGenerate, model)
    original = typed_model.generate
    requests: list[ModelRequest] = []

    async def generate(self: _ModelWithGenerate, request: ModelRequest) -> ModelResponse:
        requests.append(request)
        if request.metadata.get("prompt_id") == "retrieval_query_recovery.v1":
            return ModelResponse(
                "",
                None,
                ModelFinishReason.STOP,
                ModelInvocation("fixture", "1.0.0", "fixture", "fixture-retrieval-recovery"),
                structured_output={"queries": queries},
            )
        return await original(request)

    object.__setattr__(model, "generate", MethodType(generate, typed_model))
    return requests


def _turn(repository: LocalRepository, content: str, request_id: str):
    sequence = repository.tutor_snapshots.get(COURSE, SESSION).high_water_sequence
    return asyncio.run(
        repository.tutor_conversation(COURSE, session_id=SESSION).turn(
            ConversationTurnCommand(
                content,
                ExecutionContext(
                    PrincipalKind.HUMAN,
                    "fixture-learner",
                    COURSE,
                    CorrelationId(f"correlation-{request_id}"),
                    session_id=SESSION,
                    idempotency_key=request_id,
                ),
                sequence,
            )
        )
    )


def test_unpinned_explain_recovers_once_with_bounded_alternative_queries(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "never-indexed-token",
                    "target": "aortic valve",
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
    )
    requests = _install_recovery_model(
        model,
        ("still-no-match", "aortic valve", "unused-third-alternative"),
    )

    # Deliberately avoid the explicit source-explanation fast path: this test
    # exercises model-selected explain_concept followed by retrieval recovery.
    with LocalRepository.open(root, model_adapters=adapters) as repository:
        result = _turn(repository, "aortic valve", "semantic-recovery")

    assert result.status is TutorHostRunStatus.COMPLETED
    assert "three cusps" in result.presentation.content
    assert (
        sum(
            request.metadata.get("prompt_id") == "retrieval_query_recovery.v1"
            for request in requests
        )
        == 1
    )
    assert [request.metadata.get("prompt_id") for request in requests] == [
        "tutor_decision.v1",
        "retrieval_query_recovery.v1",
        "explain_concept.v1",
    ]
    assert "Valve notes" in requests[1].messages[-1].content
    explain_request = requests[-1]
    assert "never-indexed-token" in "\n".join(
        message.content for message in explain_request.messages
    )


def test_unpinned_explain_that_hits_initially_does_not_call_recovery_model(
    tmp_path: Path,
) -> None:
    root, adapters, model = _repository(
        tmp_path,
        (
            {
                "kind": "start_capability",
                "capability_id": "explain_concept",
                "inputs": {
                    "query": "aortic valve",
                    "target": "aortic valve",
                    "language": "en",
                    "learner_goal": None,
                    "continuation_summary_json": None,
                },
            },
        ),
    )
    requests = _install_recovery_model(model, ("should-not-be-used",))

    with LocalRepository.open(root, model_adapters=adapters) as repository:
        result = _turn(repository, "aortic valve", "initial-hit")

    assert result.status is TutorHostRunStatus.COMPLETED
    assert "three cusps" in result.presentation.content
    assert not any(
        request.metadata.get("prompt_id") == "retrieval_query_recovery.v1" for request in requests
    )
    assert [request.metadata.get("prompt_id") for request in requests] == [
        "tutor_decision.v1",
        "explain_concept.v1",
    ]
