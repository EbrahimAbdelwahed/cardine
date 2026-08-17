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
    ModelMessage,
    ModelRequest,
    StructuredOutputConstraint,
)


class _Transport:
    def __init__(self, response: HttpResponse | Exception) -> None:
        self.response = response
        self.calls: list[bytes] = []

    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        del url, headers, timeout_seconds
        self.calls.append(body)
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


def _model(response: HttpResponse | Exception) -> tuple[OpenAICompatibleModel, _Transport]:
    transport = _Transport(response)
    return (
        OpenAICompatibleModel(
            OpenAICompatibleConfig(
                "https://example.invalid/v1/chat/completions",
                "model",
                "secret",
                capabilities=ModelCapabilities(structured_output=True),
            ),
            transport,
        ),
        transport,
    )


def _response(value: object, status: int = 200) -> HttpResponse:
    return HttpResponse(status, json.dumps(value).encode())


def test_provider_projection_removes_validation_only_keyword_without_mutating_local_schema() -> (
    None
):
    adapter, transport = _model(
        _response(
            {
                "choices": [
                    {
                        "message": {"content": '{"items":["a"],"uniqueItems":true}'},
                        "finish_reason": "stop",
                    }
                ]
            }
        )
    )
    request = ModelRequest(
        (ModelMessage(MessageRole.USER, "q"),),
        StructuredOutputConstraint(
            "answer",
            {
                "type": "object",
                "properties": {
                    "items": {
                        "type": "array",
                        "items": {"type": "string"},
                        "maxItems": 3,
                        "uniqueItems": True,
                    },
                    "uniqueItems": {"type": "boolean"},
                },
                "required": ("items", "uniqueItems"),
                "additionalProperties": False,
            },
        ),
    )

    result = asyncio.run(adapter.generate(request))
    sent: dict[str, Any] = json.loads(transport.calls[0])
    provider_schema = sent["response_format"]["json_schema"]["schema"]

    assert "uniqueItems" not in provider_schema["properties"]["items"]
    assert provider_schema["properties"]["items"]["maxItems"] == 3
    assert "uniqueItems" in provider_schema["properties"]
    assert request.structured_output is not None
    assert request.structured_output.schema["properties"]["items"]["uniqueItems"] is True
    assert result.structured_output is not None
    assert result.structured_output["items"] == ("a",)
    assert result.structured_output["uniqueItems"] is True


@pytest.mark.parametrize(
    ("status", "provider_code", "expected"),
    (
        (403, None, ModelErrorCode.AUTHORIZATION),
        (400, "invalid_json_schema", ModelErrorCode.SCHEMA_INCOMPATIBLE),
    ),
)
def test_provider_authorization_and_schema_failures_remain_distinct(
    status: int, provider_code: str | None, expected: ModelErrorCode
) -> None:
    body: dict[str, object] = {}
    if provider_code is not None:
        body = {"error": {"code": provider_code}}
    adapter, _ = _model(_response(body, status))

    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, "q"),))))

    assert caught.value.code is expected
    assert not caught.value.retryable


def test_typed_transport_model_error_is_not_collapsed_to_unavailable() -> None:
    adapter, _ = _model(ModelError(ModelErrorCode.SCHEMA_INCOMPATIBLE, "provider schema rejected"))

    with pytest.raises(ModelError) as caught:
        asyncio.run(adapter.generate(ModelRequest((ModelMessage(MessageRole.USER, "q"),))))

    assert caught.value.code is ModelErrorCode.SCHEMA_INCOMPATIBLE
