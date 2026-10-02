"""Versioned Luna presets over the bounded Chat Completions transport."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field

from cardine.adapters.model.diagnostic_transport import DiagnosticHttpTransport
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
GPT_6_LUNA_ADAPTER_ID = "openai-gpt-6-luna"
GPT_6_LUNA_ADAPTER_VERSION = "1.0.0"
GPT_6_LUNA_MODEL_ID = "gpt-6-luna"
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
    _model_id = GPT_5_6_LUNA_MODEL_ID

    def __init__(
        self,
        config: OpenAIGpt56LunaConfig,
        *,
        transport: HttpTransport | None = None,
    ) -> None:
        if not isinstance(config, OpenAIGpt56LunaConfig):
            raise TypeError("config must be OpenAIGpt56LunaConfig")
        super().__init__(
            OpenAICompatibleConfig(
                GPT_5_6_LUNA_ENDPOINT,
                self._model_id,
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
                    f"{self._model_id} structured output must be strict",
                )
            return await super().generate(request)


class OpenAIGpt6LunaModel(OpenAIGpt56LunaModel):
    """GPT-6 Luna with its own invocation identity and the same strict contract."""

    _adapter_id = GPT_6_LUNA_ADAPTER_ID
    _adapter_version = GPT_6_LUNA_ADAPTER_VERSION
    _model_id = GPT_6_LUNA_MODEL_ID


__all__ = [
    "GPT_5_6_LUNA_ADAPTER_ID",
    "GPT_5_6_LUNA_ADAPTER_VERSION",
    "GPT_5_6_LUNA_ENDPOINT",
    "GPT_5_6_LUNA_MODEL_ID",
    "GPT_5_6_LUNA_REASONING_EFFORT",
    "GPT_6_LUNA_ADAPTER_ID",
    "GPT_6_LUNA_ADAPTER_VERSION",
    "GPT_6_LUNA_MODEL_ID",
    "OpenAIGpt6LunaModel",
    "OpenAIGpt56LunaConfig",
    "OpenAIGpt56LunaModel",
]
