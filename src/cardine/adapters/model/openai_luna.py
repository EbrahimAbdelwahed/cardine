"""Fixed GPT-5.6 Luna preset over the bounded Chat Completions transport."""

from __future__ import annotations

from dataclasses import dataclass, field

from study_agent.adapters.model.openai_compatible import (
    HttpTransport,
    OpenAICompatibleConfig,
    OpenAICompatibleModel,
)
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


@dataclass(frozen=True, slots=True)
class OpenAIGpt56LunaConfig:
    """Credential and timeout only; endpoint/model/effort are not configurable."""

    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0

    def __post_init__(self) -> None:
        if (
            not isinstance(self.api_key, str)
            or not self.api_key
            or any(
                ord(character) < 32 or ord(character) == 127
                for character in self.api_key
            )
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
    ) -> None:
        if not isinstance(config, OpenAIGpt56LunaConfig):
            raise TypeError("config must be OpenAIGpt56LunaConfig")
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
            transport,
        )

    async def generate(self, request: ModelRequest) -> ModelResponse:
        if (
            request.structured_output is not None
            and request.structured_output.strict is not True
        ):
            raise ModelError(
                ModelErrorCode.PROTOCOL_ERROR,
                "GPT-5.6 Luna structured output must be strict",
            )
        return await super().generate(request)


__all__ = [
    "GPT_5_6_LUNA_ADAPTER_ID",
    "GPT_5_6_LUNA_ADAPTER_VERSION",
    "GPT_5_6_LUNA_ENDPOINT",
    "GPT_5_6_LUNA_MODEL_ID",
    "GPT_5_6_LUNA_REASONING_EFFORT",
    "OpenAIGpt56LunaConfig",
    "OpenAIGpt56LunaModel",
]
