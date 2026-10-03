"""Fixed GPT-5.6 Luna preset over the bounded Chat Completions transport."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import aclosing
from dataclasses import dataclass, field

from cardine.adapters.model.diagnostic_transport import DiagnosticHttpTransport
from cardine.adapters.model.streaming import (
    MAX_STREAM_BYTES,
    HttpxStreamingTransport,
    StreamingTransport,
    draft_text,
)
from cardine.demo.turn_output import output_observer
from cardine.diagnostics.turn_trace import trace_operation
from study_agent.adapters.model.openai_compatible import (
    HttpTransport,
    OpenAICompatibleConfig,
    OpenAICompatibleModel,
    StdlibHttpTransport,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports.model import (
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelRequest,
    ModelResponse,
)

GPT_5_6_LUNA_ADAPTER_ID = "openai-gpt-5.6-luna"
GPT_5_6_LUNA_ADAPTER_VERSION = "1.0.0"
GPT_5_6_LUNA_MODEL_ID = "gpt-5.6-luna"
GPT_5_6_LUNA_ENDPOINT = "https://api.openai.com/v1/chat/completions"
GPT_5_6_LUNA_REASONING_EFFORT = "none"
_UNSUPPORTED_STRICT_SCHEMA_KEYWORDS = frozenset({"uniqueItems"})


def _provider_strict_schema(value: object) -> object:
    """Project local constraints into the strict provider schema without mutation."""

    if isinstance(value, Mapping):
        projected = {
            str(key): _provider_strict_schema(item)
            for key, item in value.items()
            if key not in _UNSUPPORTED_STRICT_SCHEMA_KEYWORDS
        }
        # Locally an empty-only array needs no element schema. The provider
        # still requires one; maxItems=0 preserves the exact accepted values.
        if value.get("type") == "array" and value.get("maxItems") == 0:
            projected.setdefault("items", {"type": "string"})
        return projected
    if isinstance(value, tuple):
        return tuple(_provider_strict_schema(item) for item in value)
    return value


@dataclass(frozen=True, slots=True)
class OpenAIGpt56LunaConfig:
    """Credential and timeout only; endpoint/model/effort are not configurable."""

    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.api_key, str)
            or not self.api_key
            or any(ord(character) < 32 or ord(character) == 127 for character in self.api_key)
        ):
            raise ValueError("api_key must be non-empty bounded text")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= 300
        ):
            raise ValueError("timeout_seconds must be positive and bounded")


class OpenAIGpt56LunaModel(OpenAICompatibleModel):
    """Expose the explicit Luna baseline through the provider-neutral ModelPort."""

    _adapter_id = GPT_5_6_LUNA_ADAPTER_ID
    _adapter_version = GPT_5_6_LUNA_ADAPTER_VERSION

    def __init__(
        self,
        config: OpenAIGpt56LunaConfig,
        *,
        transport: HttpTransport | None = None,
        streaming_transport: StreamingTransport | None = None,
    ) -> None:
        if not isinstance(config, OpenAIGpt56LunaConfig):
            raise TypeError("config must be OpenAIGpt56LunaConfig")
        self._streaming_transport = (
            streaming_transport
            if streaming_transport is not None
            else HttpxStreamingTransport()
            if transport is None
            else None
        )
        super().__init__(
            OpenAICompatibleConfig(
                GPT_5_6_LUNA_ENDPOINT,
                GPT_5_6_LUNA_MODEL_ID,
                config.api_key,
                config.timeout_seconds,
                ModelCapabilities(structured_output=True),
                reasoning_effort=GPT_5_6_LUNA_REASONING_EFFORT,
                max_output_tokens_field="max_completion_tokens",
            ),
            DiagnosticHttpTransport(transport if transport is not None else StdlibHttpTransport()),
        )

    def _structured_output_schema(self, schema: JsonObject) -> object:
        return _provider_strict_schema(schema)

    async def generate(self, request: ModelRequest) -> ModelResponse:
        with trace_operation("model_generation"):
            if (
                request.structured_output is not None
                and request.structured_output.strict is not True
            ):
                raise ModelError(
                    ModelErrorCode.PROTOCOL_ERROR,
                    "GPT-5.6 Luna structured output must be strict",
                )
            observer = output_observer()
            if (
                observer is not None
                and self._streaming_transport is not None
                and request.structured_output is not None
                and request.structured_output.name == "explain_concept_draft"
            ):
                return await self._generate_streaming(request, observer)
            return await super().generate(request)

    async def _generate_streaming(
        self, request: ModelRequest, observer: Callable[[str], None]
    ) -> ModelResponse:
        assert self._streaming_transport is not None
        payload = json.loads(self._body(request))
        payload["stream"] = True
        payload["stream_options"] = {"include_usage": True}
        body = json.dumps(payload, ensure_ascii=False).encode()
        content = ""
        finish_reason: str | None = None
        response_id: str | None = None
        usage: object = None
        observer("")
        try:
            async with asyncio.timeout(self._config.timeout_seconds), aclosing(
                self._streaming_transport.events(
                    self._config.endpoint_url, self._headers(), body, self._config.timeout_seconds
                )
            ) as events:
                async for raw in events:
                    if raw == "[DONE]":
                        if finish_reason is None:
                            raise ValueError("stream is incomplete")
                        response = {
                            "id": response_id,
                            "choices": [
                                {
                                    "message": {"role": "assistant", "content": content},
                                    "finish_reason": finish_reason,
                                }
                            ],
                            "usage": usage,
                        }
                        return self._parse(json.dumps(response).encode(), request)
                    frame = json.loads(raw)
                    if not isinstance(frame, dict):
                        raise ValueError("invalid stream frame")
                    if isinstance(frame.get("id"), str):
                        response_id = frame["id"]
                    if frame.get("usage") is not None:
                        usage = frame["usage"]
                    for choice in frame.get("choices", []):
                        if choice.get("index") != 0:
                            raise ValueError("unexpected stream choice")
                        delta = choice.get("delta", {})
                        if delta.get("tool_calls") or delta.get("refusal"):
                            raise ValueError("stream does not contain a grounded answer")
                        text = delta.get("content")
                        if text is not None:
                            if not isinstance(text, str):
                                raise ValueError("invalid stream content")
                            content += text
                            if len(content.encode()) > MAX_STREAM_BYTES:
                                raise ValueError("stream exceeded limit")
                            observer(draft_text(content))
                        if choice.get("finish_reason") is not None:
                            finish_reason = choice["finish_reason"]
            raise ValueError("stream ended before completion")
        except TimeoutError:
            raise ModelError(
                ModelErrorCode.TIMEOUT, "model stream timed out", retryable=True
            ) from None
        except (ValueError, TypeError, KeyError, AttributeError):
            raise ModelError(ModelErrorCode.PROTOCOL_ERROR, "model stream is invalid") from None


__all__ = [
    "GPT_5_6_LUNA_ADAPTER_ID",
    "GPT_5_6_LUNA_ADAPTER_VERSION",
    "GPT_5_6_LUNA_ENDPOINT",
    "GPT_5_6_LUNA_MODEL_ID",
    "GPT_5_6_LUNA_REASONING_EFFORT",
    "OpenAIGpt56LunaConfig",
    "OpenAIGpt56LunaModel",
]
