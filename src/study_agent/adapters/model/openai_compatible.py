"""Dependency-free generic OpenAI-compatible chat-completions adapter."""

from __future__ import annotations

import asyncio
import json
import re
import urllib.error
import urllib.request
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Protocol, cast
from urllib.parse import urlsplit

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.ports.model import (
    CancellationToken,
    MessageRole,
    ModelCapabilities,
    ModelError,
    ModelErrorCode,
    ModelFinishReason,
    ModelInvocation,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    ModelUsage,
    ToolCall,
)

ADAPTER_ID = "openai-compatible-http"
ADAPTER_VERSION = "1.0.0"
_MAX_RESPONSE_BYTES = 8 * 1024 * 1024
_RESERVED_HEADERS = frozenset({"authorization", "content-type", "content-length"})
_HEADER_NAME = re.compile(r"^[!#$%&'*+.^_`|~0-9A-Za-z-]+$")
_STRUCTURED_OUTPUT_FORMATS = frozenset({"json_schema", "json_object"})
_REASONING_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
_MAX_OUTPUT_TOKEN_FIELDS = frozenset({"max_tokens", "max_completion_tokens"})
_PROVIDER_LOCAL_VALIDATION_ONLY_KEYWORDS = frozenset({"minLength", "uniqueItems"})
_PROVIDER_SCHEMA_ERROR_CODES = frozenset(
    {
        "invalid_json_schema",
        "invalid_schema",
        "json_schema_invalid",
        "schema_validation_error",
        "unsupported_schema",
    }
)


@dataclass(frozen=True, slots=True)
class HttpResponse:
    status: int
    body: bytes


class HttpTransport(Protocol):
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse: ...


class _TransportFailure(Exception):
    pass


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep bearer credentials on the configured origin only."""

    def redirect_request(
        self,
        request: urllib.request.Request,
        file_pointer: object,
        status: int,
        message: str,
        headers: object,
        new_url: str,
    ) -> None:
        return None


class StdlibHttpTransport:
    def post(
        self,
        url: str,
        headers: Mapping[str, str],
        body: bytes,
        timeout_seconds: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url, body, dict(headers), method="POST")
        try:
            opener = urllib.request.build_opener(_NoRedirectHandler())
            with opener.open(request, timeout=timeout_seconds) as response:
                payload = response.read(_MAX_RESPONSE_BYTES + 1)
                status = response.status
        except urllib.error.HTTPError as error:
            payload = error.read(_MAX_RESPONSE_BYTES + 1)
            status = error.code
        except (urllib.error.URLError, OSError) as error:
            raise _TransportFailure from error
        if len(payload) > _MAX_RESPONSE_BYTES:
            raise _TransportFailure
        return HttpResponse(status, payload)


@dataclass(frozen=True, slots=True)
class OpenAICompatibleConfig:
    endpoint_url: str
    model_id: str
    api_key: str = field(repr=False)
    timeout_seconds: float = 60.0
    capabilities: ModelCapabilities = field(default_factory=ModelCapabilities)
    extra_headers: Mapping[str, str] = field(default_factory=dict, repr=False)
    structured_output_format: str = "json_schema"
    reasoning_effort: str | None = None
    max_output_tokens_field: str = "max_tokens"

    def __post_init__(self) -> None:
        parsed = urlsplit(self.endpoint_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("endpoint_url must be an absolute HTTP(S) URL")
        if parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("endpoint_url cannot contain credentials, query, or fragment")
        if not self.model_id or self.model_id != self.model_id.strip():
            raise ValueError("model_id must be non-empty trimmed text")
        if not self.api_key:
            raise ValueError("api_key must be non-empty")
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.capabilities.streaming or self.capabilities.cancellation:
            raise ValueError("HTTP streaming and cancellation are unsupported in v0.1")
        if self.structured_output_format not in _STRUCTURED_OUTPUT_FORMATS:
            raise ValueError("structured_output_format must be json_schema or json_object")
        if self.reasoning_effort is not None and self.reasoning_effort not in _REASONING_EFFORTS:
            raise ValueError("reasoning_effort is unsupported")
        if self.max_output_tokens_field not in _MAX_OUTPUT_TOKEN_FIELDS:
            raise ValueError("max_output_tokens_field is unsupported")
        headers = dict(self.extra_headers)
        for name, value in headers.items():
            if not isinstance(name, str) or _HEADER_NAME.fullmatch(name) is None:
                raise ValueError("extra header names must be valid HTTP tokens")
            if not isinstance(value, str) or not value:
                raise ValueError("extra headers must have non-empty names and values")
            if any(ord(character) < 32 or ord(character) == 127 for character in value):
                raise ValueError("extra header values cannot contain control characters")
            if name.lower() in _RESERVED_HEADERS:
                raise ValueError("extra headers cannot override reserved transport headers")
        object.__setattr__(self, "extra_headers", MappingProxyType(headers))


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


def _provider_schema(value: object, *, property_map: bool = False) -> object:
    """Compile the full local schema into the provider's generation subset.

    Validation-only constraints remain on the immutable ``ModelRequest`` and
    are still enforced by Cardine's local validators.  Property names are data,
    so a user property literally named ``uniqueItems`` is preserved while the
    unsupported schema keyword is removed.
    """

    if isinstance(value, Mapping):
        projected: dict[str, object] = {}
        for key, item in value.items():
            name = str(key)
            if not property_map and name in _PROVIDER_LOCAL_VALIDATION_ONLY_KEYWORDS:
                continue
            projected[name] = _provider_schema(
                item,
                property_map=not property_map and name == "properties",
            )
        return projected
    if isinstance(value, (tuple, list)):
        return [_provider_schema(item) for item in value]
    return value


def _provider_error_code(body: bytes) -> str | None:
    """Extract only a bounded provider error code, never its response text."""

    try:
        payload = json.loads(body)
    except (UnicodeError, json.JSONDecodeError):
        return None
    if not isinstance(payload, Mapping):
        return None
    error = payload.get("error")
    if not isinstance(error, Mapping):
        return None
    code = error.get("code")
    if not isinstance(code, str) or len(code) > 80:
        return None
    return code


class OpenAICompatibleModel:
    _adapter_id = ADAPTER_ID
    _adapter_version = ADAPTER_VERSION

    def __init__(
        self,
        config: OpenAICompatibleConfig,
        transport: HttpTransport | None = None,
    ) -> None:
        self._config = config
        self._transport = transport or StdlibHttpTransport()

    @property
    def capabilities(self) -> ModelCapabilities:
        return self._config.capabilities

    def _structured_output_schema(self, schema: JsonObject) -> object:
        """Translate a local schema for this provider without mutating its contract."""

        return _provider_schema(schema)

    def _body(self, request: ModelRequest) -> bytes:
        messages: list[dict[str, object]] = []
        for message in request.messages:
            item: dict[str, object] = {
                "role": message.role.value,
                "content": message.content,
            }
            if message.name is not None:
                item["name"] = message.name
            if message.role is MessageRole.TOOL:
                item["tool_call_id"] = message.tool_call_id
            messages.append(item)
        payload: dict[str, object] = {
            "model": self._config.model_id,
            "messages": messages,
        }
        if request.max_output_tokens is not None:
            payload[self._config.max_output_tokens_field] = request.max_output_tokens
        if request.temperature is not None:
            payload["temperature"] = request.temperature
        if self._config.reasoning_effort is not None:
            payload["reasoning_effort"] = self._config.reasoning_effort
        if request.structured_output is not None and self.capabilities.structured_output:
            if self._config.structured_output_format == "json_object":
                payload["response_format"] = {"type": "json_object"}
            else:
                payload["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {
                        "name": request.structured_output.name,
