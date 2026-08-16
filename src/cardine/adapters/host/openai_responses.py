"""Optional OpenAI Responses adapter for the bounded tutor host.

The adapter is deliberately a thin translation boundary.  It has no gateway,
state, event, file, or provider-owned loop authority; the host runner owns all
retry and capability effects.
"""

from __future__ import annotations

import importlib
import inspect
import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol, cast

from cardine.hosts.contracts import (
    TutorDecision,
    TutorHostContext,
    decision_from_bytes,
    decision_schema,
)
from study_agent.ports.tutor_host import (
    RetryableTutorDecisionError,
    TutorDecisionPort,
    TutorInterruptionToken,
)

HOST_INSTRUCTION = "study-agent tutor host decision protocol v1"
_MAX_MODEL_ID_LENGTH = 128
_MAX_ENV_NAME_LENGTH = 128
_MAX_TIMEOUT_SECONDS = 300.0
_MAX_OUTPUT_TOKENS = 16_384
_MAX_OUTPUT_TEXT_CHARS = 64_000
_MODEL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_MISSING = object()
_SAFE_ADAPTER_MESSAGES = frozenset(
    {
        "tutor decision interrupted",
        "provider response was incomplete",
        "provider response contained an error",
        "provider response envelope is invalid",
        "provider response content is invalid",
        "provider response contained non-text output",
        "provider response text is invalid",
        "provider response text is oversized",
        "provider decision was invalid",
    }
)


class OpenAIResponsesAdapterError(RuntimeError):
    """Safe, non-retryable adapter failure with no provider payload."""


class OpenAIResponsesConfigurationError(OpenAIResponsesAdapterError):
    """The optional SDK or API-key configuration is unavailable or invalid."""


class OpenAIResponsesClient(Protocol):
    responses: OpenAIResponsesResource


class OpenAIResponsesResource(Protocol):
    async def create(self, **kwargs: object) -> object: ...


@dataclass(frozen=True, slots=True)
class OpenAIResponsesTutorConfig:
    """Explicit, bounded API-key configuration (never stores the key)."""

    model_id: str
    api_key_env: str
    timeout_seconds: float = 60.0
    max_output_tokens: int = 2_048

    def __post_init__(self) -> None:
        if (
            not isinstance(self.model_id, str)
            or not self.model_id
            or len(self.model_id) > _MAX_MODEL_ID_LENGTH
            or _MODEL_ID.fullmatch(self.model_id) is None
        ):
            raise ValueError("model_id must be bounded provider-neutral text")
        if (
            not isinstance(self.api_key_env, str)
            or not self.api_key_env
            or len(self.api_key_env) > _MAX_ENV_NAME_LENGTH
            or _ENV_NAME.fullmatch(self.api_key_env) is None
        ):
            raise ValueError("api_key_env must be an environment variable name")
        if (
            isinstance(self.timeout_seconds, bool)
            or not isinstance(self.timeout_seconds, (int, float))
            or not 0 < self.timeout_seconds <= _MAX_TIMEOUT_SECONDS
        ):
            raise ValueError("timeout_seconds must be positive and bounded")
        if (
            type(self.max_output_tokens) is not int
            or not 0 < self.max_output_tokens <= _MAX_OUTPUT_TOKENS
        ):
            raise ValueError("max_output_tokens must be positive and bounded")


class OpenAIResponsesTutorDecisionPort(TutorDecisionPort):
    """Translate one host decision through the optional Responses SDK."""

    def __init__(
        self,
        config: OpenAIResponsesTutorConfig,
        *,
        client: OpenAIResponsesClient | None = None,
    ) -> None:
        if not isinstance(config, OpenAIResponsesTutorConfig):
            raise TypeError("config must be OpenAIResponsesTutorConfig")
        self._config = config
        self._client = client

    async def decide(
        self,
        context: TutorHostContext,
        interruption: TutorInterruptionToken,
    ) -> TutorDecision:
        if interruption.is_interrupted():
            raise OpenAIResponsesAdapterError("tutor decision interrupted")
        owned = False
        client = self._client
        if client is None:
            client = self._build_default_client()
            owned = True
        try:
            if interruption.is_interrupted():
                raise OpenAIResponsesAdapterError("tutor decision interrupted")
            response = await client.responses.create(**self._request(context))
            if interruption.is_interrupted():
                raise OpenAIResponsesAdapterError("tutor decision interrupted")
            return self._parse_response(response, context)
        except RetryableTutorDecisionError:
            # Never relay an injected provider exception message: it may carry
            # response bodies, headers, request ids, or other secrets.
            raise RetryableTutorDecisionError("provider request is retryable") from None
        except OpenAIResponsesAdapterError as error:
            if str(error) in _SAFE_ADAPTER_MESSAGES:
                raise
            raise OpenAIResponsesAdapterError("provider request failed") from None
        except Exception as error:
            raise _map_provider_error(error) from None
        finally:
            if owned:
                await _close_client(client)

    def _request(self, context: TutorHostContext) -> dict[str, object]:
        # Keep the provider input a single SDK-valid user message.  The
        # canonical context bytes are the boundary representation; passing a
        # raw mapping here would let provider SDK coercion change ordering or
        # numeric/string semantics.
        canonical_context_json = context.to_bytes().decode("utf-8")
        return {
            "model": self._config.model_id,
            "instructions": HOST_INSTRUCTION,
            "input": [
                {
                    "role": "user",
                    "content": [{"type": "input_text", "text": canonical_context_json}],
                }
            ],
            "text": {
                "format": {
                    "type": "json_schema",
                    "name": "study_agent_tutor_decision",
                    "strict": True,
                    "schema": _plain(_provider_strict_schema(decision_schema(context))),
                }
            },
            "store": False,
            "max_output_tokens": self._config.max_output_tokens,
        }

    def _build_default_client(self) -> OpenAIResponsesClient:
        key = os.environ.get(self._config.api_key_env)
        if not isinstance(key, str) or not key or any(
            ord(character) < 32 or ord(character) == 127 for character in key
        ):
            raise OpenAIResponsesConfigurationError("API key environment is unavailable")
        try:
            module = importlib.import_module("openai")
            factory = module.AsyncOpenAI
            client = factory(
                api_key=key,
                timeout=self._config.timeout_seconds,
                max_retries=0,
            )
        except OpenAIResponsesAdapterError:
            raise
        except (ImportError, AttributeError):
            raise OpenAIResponsesConfigurationError(
                "optional OpenAI SDK is unavailable"
            ) from None
        except Exception:
            raise OpenAIResponsesConfigurationError(
                "OpenAI client could not be constructed"
            ) from None
        if not hasattr(client, "responses"):
            raise OpenAIResponsesConfigurationError("OpenAI client is incompatible")
        return cast(OpenAIResponsesClient, client)

    @staticmethod
    def _parse_response(response: object, context: TutorHostContext) -> TutorDecision:
        status = _field(response, "status")
        if status != "completed":
            raise OpenAIResponsesAdapterError("provider response was incomplete")
        if _field(response, "error") not in (_MISSING, None):
            raise OpenAIResponsesAdapterError("provider response contained an error")
        if _field(response, "incomplete_details") not in (_MISSING, None):
            raise OpenAIResponsesAdapterError("provider response was incomplete")
        output = _field(response, "output")
        if not isinstance(output, Sequence) or isinstance(output, (str, bytes, bytearray)):
            raise OpenAIResponsesAdapterError("provider response envelope is invalid")
        assistant_messages = 0
        output_texts: list[str] = []
        for item in output:
            item_type = _field(item, "type")
            if item_type == "reasoning":
                continue
            if item_type != "message" or _field(item, "role") != "assistant":
                raise OpenAIResponsesAdapterError("provider response envelope is invalid")
            assistant_messages += 1
            content = _field(item, "content")
            if not isinstance(content, Sequence) or isinstance(
                content, (str, bytes, bytearray)
            ):
                raise OpenAIResponsesAdapterError("provider response content is invalid")
            for block in content:
                if _field(block, "type") != "output_text":
                    raise OpenAIResponsesAdapterError("provider response contained non-text output")
                text = _field(block, "text")
                if not isinstance(text, str) or not text.strip():
                    raise OpenAIResponsesAdapterError("provider response text is invalid")
                if len(text) > _MAX_OUTPUT_TEXT_CHARS:
                    raise OpenAIResponsesAdapterError("provider response text is oversized")
                output_texts.append(text)
        if assistant_messages != 1 or len(output_texts) != 1:
            raise OpenAIResponsesAdapterError("provider response envelope is invalid")
        try:
            decoded = json.loads(output_texts[0])
            if not isinstance(decoded, dict) or set(decoded) != {"decision"}:
                raise ValueError
            decision = decoded["decision"]
            local_schema = decision_schema(context)
            decision_schema_value = local_schema["properties"]["decision"]
            cleaned_decision = _remove_provider_null_optionals(
                decision, decision_schema_value
            )
            encoded = json.dumps(
                _plain(cleaned_decision),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode()
            return decision_from_bytes(encoded, context)
        except Exception:
            raise OpenAIResponsesAdapterError("provider decision was invalid") from None


def _field(value: object, name: str) -> object:
    if isinstance(value, Mapping):
        return value.get(name, _MISSING)
    return getattr(value, name, _MISSING)


def _plain(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    if isinstance(value, list):
        return [_plain(item) for item in value]
    return value


def _provider_strict_schema(value: object) -> object:
    """Project optional local object fields into OpenAI strict JSON schema."""

    if isinstance(value, Mapping):
        projected = {str(key): _provider_strict_schema(item) for key, item in value.items()}
        properties = projected.get("properties")
        required = projected.get("required")
        if isinstance(properties, dict) and isinstance(required, tuple):
            required_names = set(required)
            for name, item in tuple(properties.items()):
                if name not in required_names:
                    properties[name] = {"anyOf": (item, {"type": "null"})}
                    required_names.add(name)
            projected["required"] = tuple(sorted(required_names))
        return projected
    if isinstance(value, tuple):
        return tuple(_provider_strict_schema(item) for item in value)
    return value


def _remove_provider_null_optionals(value: object, schema: object) -> object:
    """Remove nulls introduced by the strict provider projection."""

    if not isinstance(schema, Mapping):
        return value
    variants = schema.get("anyOf")
    if isinstance(variants, tuple):
        schema = _matching_variant(value, variants)
        if not isinstance(schema, Mapping):
            return value
    if isinstance(value, Mapping):
        properties = schema.get("properties")
        required = schema.get("required")
        if not isinstance(properties, Mapping) or not isinstance(required, tuple):
            return value
        required_names = set(required)
        cleaned: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if name not in properties:
                cleaned[name] = item
            elif item is None and name not in required_names:
                continue
            else:
                cleaned[name] = _remove_provider_null_optionals(item, properties[name])
        return cleaned
    if isinstance(value, tuple):
        items = schema.get("items")
        return tuple(_remove_provider_null_optionals(item, items) for item in value)
    if isinstance(value, list):
        items = schema.get("items")
        return [_remove_provider_null_optionals(item, items) for item in value]
    return value


def _matching_variant(value: object, variants: tuple[object, ...]) -> object:
    if not isinstance(value, Mapping):
        return variants[0] if variants else {}
    kind = value.get("kind")
    for variant in variants:
        if not isinstance(variant, Mapping):
            continue
        properties = variant.get("properties")
        if not isinstance(properties, Mapping):
            continue
        discriminator = properties.get("kind")
        if isinstance(discriminator, Mapping) and kind in discriminator.get("enum", ()):
            return variant
    return variants[0] if variants else {}


async def _close_client(client: object) -> None:
    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        result = close()
        if inspect.isawaitable(result):
            await result
    except Exception:
        return


def _map_provider_error(error: Exception) -> Exception:
    name = type(error).__name__
    if name in {"APIConnectionError", "APITimeoutError"} or isinstance(
        error, (TimeoutError, ConnectionError)
    ):
        return RetryableTutorDecisionError("provider request is retryable")
    status = getattr(error, "status_code", _MISSING)
    if type(status) is int and (
        status in {408, 409, 429} or 500 <= status <= 599
    ):
        return RetryableTutorDecisionError("provider request is retryable")
    return OpenAIResponsesAdapterError("provider request failed")


__all__ = [
    "HOST_INSTRUCTION",
    "OpenAIResponsesAdapterError",
    "OpenAIResponsesClient",
    "OpenAIResponsesConfigurationError",
    "OpenAIResponsesResource",
    "OpenAIResponsesTutorConfig",
    "OpenAIResponsesTutorDecisionPort",
]
