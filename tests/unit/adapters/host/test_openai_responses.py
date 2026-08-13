from __future__ import annotations

import asyncio
from dataclasses import dataclass

import pytest

from cardine.adapters.host import (
    OpenAIResponsesAdapterError,
    OpenAIResponsesClient,
    OpenAIResponsesResource,
    OpenAIResponsesTutorConfig,
    OpenAIResponsesTutorDecisionPort,
)
from cardine.hosts import AdvertisedCapability, TutorHostContext
from study_agent.ports import RetryableTutorDecisionError

_SHA = "a" * 64


class _Interruption:
    def __init__(self, interrupted: bool = False) -> None:
        self.interrupted = interrupted

    def is_interrupted(self) -> bool:
        return self.interrupted


@dataclass
class _Responses(OpenAIResponsesResource):
    response: object
    error: Exception | None = None
    request: dict[str, object] | None = None

    async def create(self, **kwargs: object) -> object:
        self.request = kwargs
        if self.error is not None:
            raise self.error
        return self.response


@dataclass
class _Client(OpenAIResponsesClient):
    responses: OpenAIResponsesResource
    closed: bool = False

    async def close(self) -> None:
        self.closed = True


def _context() -> TutorHostContext:
    return TutorHostContext(
        course_id="course",
        session_id="session",
        tutor_snapshot_sequence=1,
        learner_evidence_through_sequence=1,
        tutor_snapshot={"status": "active"},
        learner_evidence={"estimates": ()},
        advertised_capabilities=(
            AdvertisedCapability(
                "grounding.ask",
                "grounding.ask@1.0.0",
                _SHA,
                {
                    "type": "object",
                    "properties": {"topic": {"type": "string", "minLength": 1}},
                    "required": ("topic",),
                    "additionalProperties": False,
                },
                True,
            ),
        ),
    )


def _response(text: str) -> dict[str, object]:
    return {
        "status": "completed",
        "error": None,
        "incomplete_details": None,
        "output": [
            {"type": "reasoning"},
            {
                "type": "message",
                "role": "assistant",
                "content": [{"type": "output_text", "text": text}],
            },
        ],
    }


def test_responses_request_is_bounded_and_decision_is_validated() -> None:
    responses = _Responses(
        _response('{"decision":{"kind":"assistant_message","message":"Hello"}}')
    )
    client = _Client(responses)
    port = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig("gpt-5.6", "OPENAI_API_KEY"), client=client
    )

    decision = asyncio.run(port.decide(_context(), _Interruption()))

    assert decision.message == "Hello"  # type: ignore[union-attr]
    assert responses.request is not None
    assert set(responses.request) == {
        "model",
        "instructions",
        "input",
        "text",
        "store",
        "max_output_tokens",
    }
    assert responses.request["store"] is False
    assert responses.request["input"] == [
        {
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": _context().to_bytes().decode("utf-8"),
                }
            ],
        }
    ]
    assert "tools" not in responses.request
    assert "previous_response_id" not in responses.request
    assert client.closed is False


@pytest.mark.parametrize(
    "response",
    (
        {"status": "incomplete", "output": []},
        {
            "status": "completed",
            "output": [{"type": "message", "role": "assistant", "content": []}],
        },
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "refusal", "refusal": "no"}],
                }
            ],
        },
        {
            "status": "completed",
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [
                        {"type": "output_text", "text": "{}"},
                        {"type": "output_text", "text": "{}"},
                    ],
                }
            ],
        },
    ),
)
def test_malformed_or_refused_envelopes_fail_closed(response: object) -> None:
    client = _Client(_Responses(response))
    port = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig("gpt-5.6", "OPENAI_API_KEY"), client=client
    )
    with pytest.raises(OpenAIResponsesAdapterError):
        asyncio.run(port.decide(_context(), _Interruption()))


def test_interruption_before_request_has_no_client_effect() -> None:
    responses = _Responses(_response('{"decision":{"kind":"stop","reason":"completed"}}'))
    client = _Client(responses)
    port = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig("gpt-5.6", "OPENAI_API_KEY"), client=client
    )
    interruption = _Interruption(True)

    with pytest.raises(OpenAIResponsesAdapterError):
        asyncio.run(port.decide(_context(), interruption))
    assert responses.request is None


@pytest.mark.parametrize("status_code", (408, 409, 429, 500, 503, 599))
def test_retryable_provider_status_is_neutral_runner_error(status_code: int) -> None:
    class ForeignSDKStatusError(Exception):
        status_code: int

        def __init__(self, status: int) -> None:
            super().__init__("provider details must stay private")
            self.status_code = status

    client = _Client(_Responses(_response("{}"), error=ForeignSDKStatusError(status_code)))
    port = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig("gpt-5.6", "OPENAI_API_KEY"), client=client
    )
    with pytest.raises(RetryableTutorDecisionError):
        asyncio.run(port.decide(_context(), _Interruption()))


def test_retryable_provider_detail_is_not_relayed() -> None:
    secret = "authorization header and request body"
    client = _Client(_Responses(_response("{}"), error=RetryableTutorDecisionError(secret)))
    port = OpenAIResponsesTutorDecisionPort(
        OpenAIResponsesTutorConfig("gpt-5.6", "OPENAI_API_KEY"), client=client
    )
    with pytest.raises(RetryableTutorDecisionError) as error:
        asyncio.run(port.decide(_context(), _Interruption()))
    assert secret not in str(error.value)


@pytest.mark.parametrize("field", ("model_id", "api_key_env"))
def test_configuration_rejects_path_or_secret_shaped_values(field: str) -> None:
    if field == "model_id":
        with pytest.raises(ValueError):
            OpenAIResponsesTutorConfig("sk-secret/../value", "OPENAI_API_KEY")
    else:
        with pytest.raises(ValueError):
            OpenAIResponsesTutorConfig("gpt-5.6", "sk-secret/../value")
