from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator, Mapping
from typing import Any, cast

import pytest

from cardine.demo.turn_output import TurnOutputStore
from cardine.hosts import AssistantMessageDecision, TutorHostContext
from study_agent.adapters.model import (
    GPT_5_6_LUNA_ADAPTER_ID,
    GPT_5_6_LUNA_MODEL_ID,
    HttpResponse,
    ModelTutorDecisionPort,
    OpenAIGpt56LunaConfig,
    OpenAIGpt56LunaModel,
)
from study_agent.ports import (
    MessageRole,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelMessage,
    ModelRequest,
    StructuredOutputConstraint,
)
from study_agent.skills.builtin.hybrid_flashcards import HYBRID_FLASHCARDS_MODEL_SCHEMA

SECRET = "openai-secret-sentinel"


@pytest.mark.parametrize("failure", ["truncated", "timeout", "cancelled"])
def test_stream_failure_discards_draft_and_preserves_cancellation(failure: str) -> None:
    pytest.importorskip("pydantic_core")
    store = TurnOutputStore()

    class Stream:
        async def events(
            self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
        ) -> AsyncGenerator[str, None]:
            del url, headers, body, timeout_seconds
            yield json.dumps({"choices": [{"index": 0, "delta": {
                "content": '{"segments":[{"text":"Partial answer'
            }}]})
            assert store.snapshot("failure")["text"] == "Partial answer"
            if failure == "timeout":
                raise TimeoutError
            if failure == "cancelled":
                raise asyncio.CancelledError

    adapter = OpenAIGpt56LunaModel(
        OpenAIGpt56LunaConfig(SECRET), streaming_transport=Stream()
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "Explain."),),
        StructuredOutputConstraint("explain_concept_draft", {"type": "object"}),
        max_output_tokens=512, temperature=0,
    )
    if failure == "cancelled":
        with pytest.raises(asyncio.CancelledError), store.capture("failure"):
            asyncio.run(adapter.generate(request))
    else:
        with pytest.raises(ModelError) as caught, store.capture("failure"):
            asyncio.run(adapter.generate(request))
        assert caught.value.code is (
            ModelErrorCode.TIMEOUT if failure == "timeout" else ModelErrorCode.PROTOCOL_ERROR
        )
    assert store.snapshot("failure")["state"] == "unavailable"
    assert store.snapshot("failure")["text"] == ""


class FakeTransport:
    def __init__(self, response: HttpResponse) -> None:
        self.response = response
        self.calls: list[tuple[str, Mapping[str, str], bytes, float]] = []

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        self.calls.append((url, headers, body, timeout_seconds))
        return self.response


class _NeverInterrupted:
    def is_interrupted(self) -> bool:
        return False


def _tutor_context() -> TutorHostContext:
    return TutorHostContext(
        "luna-course",
        "luna-session",
        0,
        0,
        {"course_id": "luna-course", "session_id": "luna-session"},
        {"course_id": "luna-course", "through_sequence": 0, "estimates": ()},
        (),
    )


@pytest.mark.parametrize(
    ("gpt6", "model_id", "adapter_id"),
    [
        (False, GPT_5_6_LUNA_MODEL_ID, GPT_5_6_LUNA_ADAPTER_ID),
        (True, "gpt-6-luna", "openai-gpt-6-luna"),
    ],
)
def test_luna_adapter_pins_api_contract_and_records_exact_provenance(
    gpt6: bool, model_id: str, adapter_id: str
) -> None:
    from cardine.adapters.model.openai_luna import OpenAIGpt6LunaModel

    adapter_type = OpenAIGpt6LunaModel if gpt6 else OpenAIGpt56LunaModel
    transport = FakeTransport(
        HttpResponse(
            200,
            json.dumps(
                {
                    "id": "luna-response-1",
                    "choices": [
                        {
                            "message": {"content": '{"decision":"supported"}'},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 8, "completion_tokens": 3},
                }
            ).encode(),
        )
    )
    adapter = adapter_type(
        OpenAIGpt56LunaConfig(SECRET, timeout_seconds=42),
        transport=transport,
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "Return the closed decision."),),
        StructuredOutputConstraint(
            "study_agent_tutor_decision",
            {
                "type": "object",
                "required": ("decision",),
                "additionalProperties": False,
                "properties": {"decision": {"type": "string"}},
            },
        ),
        max_output_tokens=512,
        temperature=0,
        metadata={"local_only": "must-not-cross-provider-boundary"},
    )

    result = asyncio.run(adapter.generate(request))
    url, headers, body, timeout = transport.calls[0]
    sent: dict[str, Any] = json.loads(body)

    assert url == "https://api.openai.com/v1/chat/completions"
    assert headers["Authorization"] == f"Bearer {SECRET}"
    assert timeout == 42
    assert sent == {
        "max_completion_tokens": 512,
        "messages": [
            {"content": "Return the closed decision.", "role": "user"}
        ],
        "model": model_id,
        "reasoning_effort": "none",
        "response_format": {
            "json_schema": {
                "name": "study_agent_tutor_decision",
                "schema": {
                    "additionalProperties": False,
                    "properties": {"decision": {"type": "string"}},
                    "required": ["decision"],
                    "type": "object",
                },
                "strict": True,
            },
            "type": "json_schema",
        },
        "temperature": 0,
    }
    assert "local_only" not in body.decode()
    assert SECRET not in body.decode()
    assert result.finish_reason is ModelFinishReason.STOP
    assert result.structured_output == {"decision": "supported"}
    assert result.invocation.adapter_id == adapter_id
    assert result.invocation.model_id == model_id
    assert result.invocation.response_id == "luna-response-1"
    assert SECRET not in repr(adapter)


def test_luna_adapter_rejects_non_strict_structured_output_before_network_io() -> None:
    transport = FakeTransport(HttpResponse(500, b"must not be read"))
    adapter = OpenAIGpt56LunaModel(
        OpenAIGpt56LunaConfig(SECRET),
        transport=transport,
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "Return JSON."),),
        StructuredOutputConstraint(
            "unsafe_non_strict_schema",
            {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
            },
            strict=False,
        ),
    )

    with pytest.raises(ModelError) as raised:
        asyncio.run(adapter.generate(request))

    assert raised.value.code is ModelErrorCode.PROTOCOL_ERROR
    assert transport.calls == []


def test_luna_adapter_projects_local_unique_items_out_of_provider_schema() -> None:
    transport = FakeTransport(
        HttpResponse(
            200,
            json.dumps(
                {
                    "choices": [
                        {
                            "message": {"content": '{"candidate_keys":["card-1"]}'},
                            "finish_reason": "stop",
                        }
                    ]
                }
            ).encode(),
        )
    )
    adapter = OpenAIGpt56LunaModel(
        OpenAIGpt56LunaConfig(SECRET),
        transport=transport,
    )
    schema = cast(dict[str, Any], HYBRID_FLASHCARDS_MODEL_SCHEMA.value)
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "Generate cards."),),
        StructuredOutputConstraint("hybrid_flashcards", schema),
    )

    result = asyncio.run(adapter.generate(request))
    sent = json.loads(transport.calls[0][2])
    provider_array = sent["response_format"]["json_schema"]["schema"]["properties"][
        "topic_plan"
    ]["items"]["properties"]["candidate_keys"]

    assert provider_array == {
        "type": "array",
        "items": {"type": "string", "minLength": 1},
        "maxItems": 24,
    }
    local_array = schema["properties"]["topic_plan"]["items"]["properties"][
        "candidate_keys"
    ]
    assert local_array["uniqueItems"] is True
    media_array = sent["response_format"]["json_schema"]["schema"]["properties"][
        "candidates"
    ]["items"]["properties"]["media_evidence_ids"]
    assert media_array["items"] == {"type": "string"}
    assert media_array["maxItems"] == 0
    assert "items" not in schema["properties"]["candidates"]["items"]["properties"][
        "media_evidence_ids"
    ]
    assert result.structured_output == {"candidate_keys": ("card-1",)}


def test_luna_chat_completion_structured_decision_reaches_the_tutor_port() -> None:
    """Pin the actual adapter -> decision parser contract with no live key."""

    transport = FakeTransport(
        HttpResponse(
            200,
            json.dumps(
                {
                    "id": "luna-valid-decision",
                    "choices": [
                        {
                            "message": {
                                "content": json.dumps(
                                    {
                                        "decision": {
                                            "kind": "assistant_message",
                                            "message": "Studiamo un concetto alla volta.",
                                        }
                                    }
                                )
                            },
                            "finish_reason": "stop",
                        }
                    ],
                }
            ).encode(),
        )
    )
    adapter = OpenAIGpt56LunaModel(
        OpenAIGpt56LunaConfig(SECRET), transport=transport
    )

    decision = asyncio.run(
        ModelTutorDecisionPort(adapter).decide(_tutor_context(), _NeverInterrupted())
    )

    assert decision == AssistantMessageDecision("Studiamo un concetto alla volta.")
    sent = json.loads(transport.calls[0][2])
    assert sent["response_format"]["json_schema"]["strict"] is True
    assert sent["response_format"]["json_schema"]["name"] == "study_agent_tutor_decision"


@pytest.mark.parametrize("malformed_strict", (1, "true"))
def test_structured_output_constraint_rejects_non_boolean_strictness(
    malformed_strict: object,
) -> None:
    with pytest.raises(ValueError, match="strict must be a boolean"):
        StructuredOutputConstraint(
            "malformed_strictness",
            {
                "type": "object",
                "additionalProperties": False,
                "properties": {},
            },
            strict=malformed_strict,  # type: ignore[arg-type]
        )
