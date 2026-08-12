from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from cardine.hosts import (
    AdvertisedCapability,
    AssistantMessageDecision,
    PendingContinuationDescriptor,
    StartCapabilityDecision,
    TutorHostContext,
)
from study_agent.adapters.model import ModelTutorDecisionError, ModelTutorDecisionPort
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
    system_prompt = request.messages[0].content
    assert "ROUTING ORDER" in system_prompt
    assert "Tutto bene?" in system_prompt
    assert "1 to 6 informative" in system_prompt
    assert "explain_concept:" not in system_prompt
    assert "source.ingest:" not in system_prompt
    provider_payload = json.loads(request.messages[-1].content)
    assert provider_payload["course_id"] == context().course_id
    assert provider_payload["tutor_snapshot"] == context().tutor_snapshot
    assert provider_payload["decision_schema"]["additionalProperties"] is False
    # Ordinary Cardine learner turns must always select a presentation-producing
    # decision; the provider must not be offered any silent stop branch.
    provider_decision_schema = json.dumps(
        provider_payload["decision_schema"], sort_keys=True
    )
    assert '"stop"' not in provider_decision_schema


def test_decision_adapter_makes_optional_input_fields_provider_strict_and_removes_nulls() -> None:
    capability = AdvertisedCapability(
        "explain_concept",
        "explain_concept@1.0.0",
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
                    "capability_id": "explain_concept",
                    "inputs": {"topic": "Anatomia", "pace": None},
                }
            }
        )
    )

    result = asyncio.run(ModelTutorDecisionPort(model).decide(optional_context, Token()))

    assert result == StartCapabilityDecision("explain_concept", {"topic": "Anatomia"})
    schema = model.requests[0].structured_output
    assert schema is not None
    _assert_all_object_fields_are_required(schema.schema)
    provider_payload = json.loads(model.requests[0].messages[-1].content)
    _assert_all_object_fields_are_required(provider_payload["decision_schema"])


def test_system_prompt_catalog_contains_only_advertised_operations() -> None:
    capability = AdvertisedCapability(
        "explain_concept",
        "explain_concept@1.0.0",
        "a" * 64,
        {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ("topic",),
            "additionalProperties": False,
        },
        False,
    )
    dynamic_context = TutorHostContext(
        "course-1",
        "session-1",
        0,
        0,
        {
            "course_id": "course-1",
            "session_id": "session-1",
            "harness_tools": (
                {
                    "name": "source.ingest",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": (),
                        "additionalProperties": False,
                    },
                },
            ),
        },
        {"course_id": "course-1", "through_sequence": 0, "estimates": ()},
        (capability,),
    )
    model = Model(
        response(
            {
                "decision": {
                    "kind": "assistant_message",
                    "message": "Posso aiutarti a pianificare lo studio.",
                }
            }
        )
    )

    asyncio.run(ModelTutorDecisionPort(model).decide(dynamic_context, Token()))

    system_prompt = model.requests[0].messages[0].content
    assert "explain_concept:" in system_prompt
    assert "source.ingest:" in system_prompt
    assert "course.create:" not in system_prompt


def test_system_prompt_rejects_advertised_operation_without_guidance() -> None:
    capability = AdvertisedCapability(
        "study.plan",
        "study.plan@1.0.0",
        "a" * 64,
        {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ("topic",),
            "additionalProperties": False,
        },
        False,
    )
    unsupported_context = TutorHostContext(
        "course-1",
        "session-1",
        0,
        0,
        {"course_id": "course-1", "session_id": "session-1"},
        {"course_id": "course-1", "through_sequence": 0, "estimates": ()},
        (capability,),
    )

    with pytest.raises(ValueError, match="routing guidance"):
        asyncio.run(
            ModelTutorDecisionPort(
                Model(
                    response(
                        {
                            "decision": {
                                "kind": "assistant_message",
                                "message": "Safe",
                            }
                        }
                    )
                )
            ).decide(unsupported_context, Token())
        )


def test_malformed_tool_descriptor_is_absent_from_prompt_catalog() -> None:
    malformed_context = TutorHostContext(
        "course-1",
        "session-1",
        0,
        0,
        {
            "course_id": "course-1",
            "session_id": "session-1",
            "harness_tools": (
                {"name": "source.ingest", "input_schema": {"type": "broken"}},
            ),
        },
        {"course_id": "course-1", "through_sequence": 0, "estimates": ()},
        (),
    )
    model = Model(
        response(
            {
                "decision": {
                    "kind": "assistant_message",
                    "message": "Non posso usare quell'operazione.",
                }
            }
        )
    )

    asyncio.run(ModelTutorDecisionPort(model).decide(malformed_context, Token()))

    assert "source.ingest:" not in model.requests[0].messages[0].content


def test_pending_continuation_prompt_does_not_advertise_unavailable_operations() -> None:
    capability = AdvertisedCapability(
        "explain_concept",
        "explain_concept@1.0.0",
        "a" * 64,
        {
            "type": "object",
            "properties": {"topic": {"type": "string"}},
            "required": ("topic",),
            "additionalProperties": False,
        },
        True,
    )
    pending_context = TutorHostContext(
        "course-1",
        "session-1",
        0,
        0,
        {
            "course_id": "course-1",
            "session_id": "session-1",
            "harness_tools": (
                {
                    "name": "source.ingest",
                    "input_schema": {
                        "type": "object",
                        "properties": {},
                        "required": (),
                        "additionalProperties": False,
                    },
                },
            ),
        },
        {"course_id": "course-1", "through_sequence": 0, "estimates": ()},
        (capability,),
        PendingContinuationDescriptor(
            "b" * 64,
            "explain_concept@1.0.0",
            "confirm",
            "Confermi?",
            {"type": "boolean"},
        ),
    )
    model = Model(
        response(
            {
                "decision": {
                    "kind": "answer_dialogue",
                    "continuation_fingerprint": "b" * 64,
                    "response": True,
                }
            }
        )
    )

    asyncio.run(ModelTutorDecisionPort(model).decide(pending_context, Token()))

    system_prompt = model.requests[0].messages[0].content
    assert "explain_concept:" not in system_prompt
    assert "source.ingest:" not in system_prompt


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
        {"decision": {"kind": "stop", "reason": "completed"}},
        {"decision": {"kind": "stop", "reason": "needs_learner_input"}},
        {"decision": {"kind": "stop", "reason": "no_safe_action"}},
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
