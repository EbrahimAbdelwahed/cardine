from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Any

import pytest

from study_agent.adapters.model import (
    HttpResponse,
    OpenAICompatibleConfig,
    OpenAICompatibleModel,
)
from study_agent.ports import (
    MessageRole,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelMessage,
    ModelRequest,
    StructuredOutputConstraint,
)

SECRET = "secret-api-key-sentinel"
LOCAL_ONLY = "local-metadata-content-sentinel"


class FakeTransport:
    def __init__(self, response: HttpResponse | Exception) -> None:
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
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def model(
    response: HttpResponse | Exception,
    *,
    capabilities: ModelCapabilities | None = None,
    structured_output_format: str = "json_schema",
) -> tuple[OpenAICompatibleModel, FakeTransport, OpenAICompatibleConfig]:
    transport = FakeTransport(response)
    config = OpenAICompatibleConfig(
        "https://example.invalid/v1/chat/completions",
        "configured-model",
        SECRET,
        capabilities=capabilities or ModelCapabilities(),
        extra_headers={"X-Client": "study-agent", "X-Secret": SECRET},
        structured_output_format=structured_output_format,
    )
    return OpenAICompatibleModel(config, transport), transport, config


def response(payload: object, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(payload).encode())


def test_http_translation_excludes_metadata_and_parses_trusted_provenance_usage() -> None:
    adapter, transport, config = model(
        response(
            {
                "id": "provider-response-1",
                "choices": [
                    {"message": {"content": "answer"}, "finish_reason": "stop"}
                ],
                "usage": {"prompt_tokens": 4, "completion_tokens": 2},
            }
        )
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "question"),),
        max_output_tokens=100,
        temperature=0,
        metadata={"local": LOCAL_ONLY},
    )

    result = asyncio.run(adapter.generate(request))
    sent: dict[str, Any] = json.loads(transport.calls[0][2])

    assert sent == {
        "model": "configured-model",
        "messages": [{"role": "user", "content": "question"}],
        "max_tokens": 100,
        "temperature": 0,
    }
    assert LOCAL_ONLY not in transport.calls[0][2].decode()
    assert SECRET not in transport.calls[0][2].decode()
    assert result.finish_reason is ModelFinishReason.STOP
    assert result.usage is not None and result.usage.output_tokens == 2
    assert result.invocation.response_id == "provider-response-1"
    assert SECRET not in repr(config)
    assert SECRET not in repr(adapter)


def test_native_structured_output_translation_and_strict_object_parsing() -> None:
    adapter, transport, _ = model(
        response(
            {
                "choices": [
                    {
                        "message": {"content": '{"answer":"supported"}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        ),
        capabilities=ModelCapabilities(structured_output=True),
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "question"),),
        StructuredOutputConstraint(
            "answer",
            {"type": "object", "required": ("answer",)},
        ),
    )

    result = asyncio.run(adapter.generate(request))
    sent: dict[str, Any] = json.loads(transport.calls[0][2])
    assert sent["response_format"]["json_schema"]["name"] == "answer"
    assert result.structured_output == {"answer": "supported"}


def test_json_object_structured_output_translation_and_parsing() -> None:
    adapter, transport, _ = model(
        response(
            {
                "choices": [
                    {
                        "message": {"content": '{"answer":"supported"}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        ),
        capabilities=ModelCapabilities(structured_output=True),
        structured_output_format="json_object",
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "Return an answer as JSON."),),
        StructuredOutputConstraint(
            "answer",
            {"type": "object", "required": ("answer",)},
        ),
    )

    result = asyncio.run(adapter.generate(request))
    sent: dict[str, Any] = json.loads(transport.calls[0][2])

    assert sent["response_format"] == {"type": "json_object"}
    assert result.structured_output == {"answer": "supported"}


@pytest.mark.parametrize(
    ("status", "code", "retryable"),
    [
        (401, ModelErrorCode.AUTHENTICATION, False),
        (429, ModelErrorCode.RATE_LIMITED, True),
        (503, ModelErrorCode.UNAVAILABLE, True),
    ],
)
def test_http_errors_are_safe_redacted_and_retryable_by_category(
    status: int, code: ModelErrorCode, retryable: bool
) -> None:
    adapter, _, config = model(HttpResponse(status, f"raw {SECRET} question".encode()))

    with pytest.raises(ModelError) as caught:
        asyncio.run(
            adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, SECRET),)))
        )

    assert caught.value.code is code
    assert caught.value.retryable is retryable
    combined = f"{caught.value!s} {caught.value!r} {config!r}"
    assert SECRET not in combined
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    ("status", "provider_code", "code"),
    [
        (404, "model_not_found", ModelErrorCode.MODEL_UNAVAILABLE),
        (404, "not_found", ModelErrorCode.ENDPOINT_INCOMPATIBLE),
        (400, "unsupported_parameter", ModelErrorCode.ENDPOINT_INCOMPATIBLE),
    ],
)
def test_provider_error_codes_distinguish_model_and_endpoint_without_leaking_body(
    status: int, provider_code: str, code: ModelErrorCode
) -> None:
    adapter, _, config = model(
        response({"error": {"code": provider_code, "message": SECRET}}, status)
    )

    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, "q"),))))

    assert caught.value.code is code
    assert SECRET not in f"{caught.value!s} {caught.value!r} {config!r}"


def test_injected_transport_exception_is_mapped_without_secret_chaining() -> None:
    adapter, _, config = model(RuntimeError(f"transport leaked {SECRET}"))

    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, "q"),))))

    assert caught.value.code is ModelErrorCode.UNAVAILABLE
    assert caught.value.retryable
    assert caught.value.__cause__ is None
    assert SECRET not in f"{caught.value!s} {caught.value!r} {config!r}"


@pytest.mark.parametrize(
    "transport_response",
    [
        object(),
        HttpResponse(True, b"{}"),
        HttpResponse(99, b"{}"),
        HttpResponse(600, b"{}"),
        HttpResponse(200, "not-bytes"),  # type: ignore[arg-type]
        HttpResponse(200, bytearray(b"{}")),  # type: ignore[arg-type]
        HttpResponse(200, b"x" * (8 * 1024 * 1024 + 1)),
    ],
)
def test_injected_transport_response_boundary_rejects_invalid_values(
    transport_response: object,
) -> None:
    adapter, _, _ = model(transport_response)  # type: ignore[arg-type]

    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, "q"),))))

    assert caught.value.code is ModelErrorCode.PROTOCOL_ERROR
    assert caught.value.__cause__ is None


@pytest.mark.parametrize(
    "headers",
    [
        {"Bad Header": "value"},
        {"X-Test\r\nInjected": "value"},
        {"X-Test": "value\r\nInjected: yes"},
        {"X-Test": "value\tcontinued"},
        {"X-Test": "value\x7f"},
        {"X-Test": 1},
    ],
)
def test_extra_headers_reject_invalid_tokens_and_control_characters(
    headers: Mapping[str, str],
) -> None:
    with pytest.raises(ValueError, match="header"):
        OpenAICompatibleConfig(
            "https://example.invalid/v1/chat/completions",
            "model",
            SECRET,
            extra_headers=headers,
        )


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"choices": []},
        {"choices": [{"message": {"content": []}, "finish_reason": "stop"}]},
        {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]},
    ],
)
def test_malformed_success_responses_fail_as_safe_protocol_errors(payload: object) -> None:
    adapter, _, _ = model(response(payload))
    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, "q"),))))
    assert caught.value.code is ModelErrorCode.PROTOCOL_ERROR
    assert caught.value.__cause__ is None
