from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from study_agent.adapters.model import ModelTutorDecisionError, ModelTutorDecisionPort
from study_agent.hosts import (
    AdvertisedCapability,
    AssistantMessageDecision,
    StartCapabilityDecision,
    TutorHostContext,
)
from study_agent.ports import (
    CancellationToken,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
)
from study_agent.ports.tutor_host import RetryableTutorDecisionError


class Token:
    def __init__(self, interrupted: bool = False) -> None:
        self.interrupted = interrupted

    def is_interrupted(self) -> bool:
        return self.interrupted


class Model:
    capabilities = ModelCapabilities(structured_output=True)

    def __init__(self, outcome: ModelResponse | BaseException) -> None:
        self.outcome = outcome
        self.requests: list[ModelRequest] = []

    async def generate(self, request: ModelRequest) -> ModelResponse:
        self.requests.append(request)
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome

    async def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        del request
        if False:  # pragma: no cover - protocol-only async generator
            yield None
        raise AssertionError("the decision adapter does not stream")

    async def cancel(self, token: CancellationToken) -> None:
        del token
        raise AssertionError("the decision adapter does not cancel model requests")


class InterruptingModel(Model):
    def __init__(self, outcome: ModelResponse, token: Token) -> None:
        super().__init__(outcome)
        self._token = token

    async def generate(self, request: ModelRequest) -> ModelResponse:
        result = await super().generate(request)
        self._token.interrupted = True
        return result


def context() -> TutorHostContext:
    return TutorHostContext(
        "course-1",
        "session-1",
        0,
        0,
        {"course_id": "course-1", "session_id": "session-1"},
        {"course_id": "course-1", "through_sequence": 0, "estimates": ()},
        (),
    )


def response(value: object) -> ModelResponse:
    return ModelResponse(
        "",
        None,
        ModelFinishReason.STOP,
        ModelInvocation("fixture", "1.0.0", "fixture-model"),
        structured_output=value,  # type: ignore[arg-type]
    )


def test_decision_adapter_sends_canonical_context_and_returns_closed_decision() -> None:
    model = Model(
        response(
            {
                "decision": {
                    "kind": "assistant_message",
                    "message": "Su cosa vuoi concentrarti?",
                }
            }
        )
    )

    result = asyncio.run(ModelTutorDecisionPort(model).decide(context(), Token()))

    assert result == AssistantMessageDecision("Su cosa vuoi concentrarti?")
    request = model.requests[0]
    assert request.temperature == 0
    assert request.structured_output is not None
    assert request.structured_output.schema["additionalProperties"] is False
    assert request.metadata["prompt_id"] == "tutor_decision.v1"
    provider_payload = json.loads(request.messages[-1].content)
    assert provider_payload["course_id"] == context().course_id
    assert provider_payload["tutor_snapshot"] == context().tutor_snapshot
    assert provider_payload["decision_schema"]["additionalProperties"] is False


def test_decision_adapter_makes_optional_input_fields_provider_strict_and_removes_nulls() -> None:
    capability = AdvertisedCapability(
        "study.plan",
        "study.plan@1.0.0",
        "a" * 64,
        {
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
                "pace": {"type": "string", "enum": ("fast", "deep")},
            },
            "required": ("topic",),
            "additionalProperties": False,
        },
        False,
    )
    optional_context = TutorHostContext(
        "course-1",
        "session-1",
        0,
        0,
        {"course_id": "course-1", "session_id": "session-1"},
        {"course_id": "course-1", "through_sequence": 0, "estimates": ()},
        (capability,),
    )
    model = Model(
        response(
            {
                "decision": {
                    "kind": "start_capability",
                    "capability_id": "study.plan",
                    "inputs": {"topic": "Anatomia", "pace": None},
                }
            }
        )
    )

    result = asyncio.run(ModelTutorDecisionPort(model).decide(optional_context, Token()))

    assert result == StartCapabilityDecision("study.plan", {"topic": "Anatomia"})
    schema = model.requests[0].structured_output
    assert schema is not None
    _assert_all_object_fields_are_required(schema.schema)
    provider_payload = json.loads(model.requests[0].messages[-1].content)
    _assert_all_object_fields_are_required(provider_payload["decision_schema"])


def _assert_all_object_fields_are_required(schema: object) -> None:
    if isinstance(schema, dict):
        properties = schema.get("properties")
        if isinstance(properties, dict):
            assert set(properties).issubset(set(schema.get("required", ())))
        for value in schema.values():
            _assert_all_object_fields_are_required(value)
    elif isinstance(schema, (list, tuple)):
        for value in schema:
            _assert_all_object_fields_are_required(value)


@pytest.mark.parametrize(
    "value",
    (
        {"decision": {"kind": "unknown"}},
        {
            "decision": {
                "kind": "assistant_message",
                "message": "ok",
                "secret": "leak",
            }
        },
        {"decision": {"kind": "start_capability", "capability_id": "invented", "inputs": {}}},
        {"other": {}},
    ),
)
def test_decision_adapter_rejects_output_outside_closed_context_schema(
    value: object,
) -> None:
    with pytest.raises(ModelTutorDecisionError, match="invalid"):
        asyncio.run(ModelTutorDecisionPort(Model(response(value))).decide(context(), Token()))


def test_retryable_model_failure_maps_without_provider_details() -> None:
    secret = "provider-body-secret"
    adapter = ModelTutorDecisionPort(
        Model(ModelError(ModelErrorCode.RATE_LIMITED, secret, retryable=True))
    )

    with pytest.raises(RetryableTutorDecisionError) as caught:
        asyncio.run(adapter.decide(context(), Token()))

    assert secret not in str(caught.value)


def test_interruption_before_or_after_provider_call_fails_without_a_decision() -> None:
    valid = response(
        {"decision": {"kind": "assistant_message", "message": "Safe"}}
    )
    before = Model(valid)
    with pytest.raises(ModelTutorDecisionError, match="interrupted"):
        asyncio.run(ModelTutorDecisionPort(before).decide(context(), Token(True)))
    assert before.requests == []

    token = Token()
    after = InterruptingModel(valid, token)
    with pytest.raises(ModelTutorDecisionError, match="interrupted"):
        asyncio.run(ModelTutorDecisionPort(after).decide(context(), token))
    assert len(after.requests) == 1


def test_oversized_output_and_nonretryable_provider_details_are_redacted() -> None:
    with pytest.raises(ModelTutorDecisionError, match="invalid"):
        asyncio.run(
            ModelTutorDecisionPort(
                Model(
                    response(
                        {
                            "decision": {
                                "kind": "assistant_message",
                                "message": "x" * 4_001,
                            }
                        }
                    )
                )
            ).decide(context(), Token())
        )

    secret = "provider-private-response"
    with pytest.raises(ModelTutorDecisionError) as caught:
        asyncio.run(
            ModelTutorDecisionPort(
                Model(ModelError(ModelErrorCode.PROTOCOL_ERROR, secret))
            ).decide(context(), Token())
        )
    assert secret not in str(caught.value)
    assert caught.value.failure_reason == ModelErrorCode.PROTOCOL_ERROR.value
