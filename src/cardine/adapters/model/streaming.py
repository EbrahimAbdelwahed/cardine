"""Native provider SSE transport for transient grounded-answer drafts."""

from __future__ import annotations

import importlib
from collections.abc import AsyncGenerator, Mapping
from typing import Protocol

from cardine.diagnostics.turn_trace import trace_operation
from study_agent.ports.model import ModelError, ModelErrorCode

MAX_STREAM_BYTES = 2 * 1024 * 1024
MAX_ERROR_BYTES = 64 * 1024


class StreamingTransport(Protocol):
    def events(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> AsyncGenerator[str, None]: ...


class HttpxStreamingTransport:
    async def events(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> AsyncGenerator[str, None]:
        # httpx and pydantic-core are dependencies of the optional OpenAI SDK.
        # Provider-free installations never import them.
        httpx = importlib.import_module("httpx")
        completed = False
        with trace_operation("provider_http") as operation:
            try:
                async with (
                    httpx.AsyncClient(timeout=timeout_seconds, follow_redirects=False) as client,
                    client.stream("POST", url, headers=headers, content=body) as response,
                ):
                    operation.observe_http_status(response.status_code)
                    if not 200 <= response.status_code < 300:
                        from study_agent.adapters.model.openai_compatible import (
                            OpenAICompatibleModel,
                        )

                        error_body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(error_body) + len(chunk) > MAX_ERROR_BYTES:
                                error_body.clear()
                                break
                            error_body.extend(chunk)
                        raise OpenAICompatibleModel._error_for_status(
                            response.status_code, bytes(error_body)
                        )
                    data: list[str] = []
                    received = 0
                    async for line in response.aiter_lines():
                        received += len(line.encode("utf-8"))
                        if received > MAX_STREAM_BYTES:
                            raise ModelError(ModelErrorCode.PROTOCOL_ERROR, "stream exceeded limit")
                        if line.startswith("data:"):
                            data.append(line[5:].lstrip(" "))
                        elif not line and data:
                            event = "\n".join(data)
                            data.clear()
                            if event == "[DONE]":
                                completed = True
                                break
                            yield event
                    if data:
                        event = "\n".join(data)
                        if event == "[DONE]":
                            completed = True
                        else:
                            yield event
            except httpx.TimeoutException:
                raise ModelError(
                    ModelErrorCode.TIMEOUT, "model stream timed out", retryable=True
                ) from None
            except httpx.HTTPError:
                raise ModelError(
                    ModelErrorCode.UNAVAILABLE, "model stream unavailable", retryable=True
                ) from None
        if completed:
            # Release the HTTP resources and finish its diagnostic span before
            # the adapter returns the final result and closes this generator.
            yield "[DONE]"


def draft_text(payload: str) -> str:
    """Extract only segment text from a partial grounded-answer JSON document.

    JSON escapes and incomplete strings are handled by the SDK's JSON parser;
    source references, tool arguments and other fields never enter the draft.
    """
    parser = importlib.import_module("pydantic_core")
    try:
        value = parser.from_json(payload.encode(), allow_partial="trailing-strings")
    except ValueError:
        return ""
    if not isinstance(value, dict) or not isinstance(value.get("segments"), list):
        return ""
    return "\n\n".join(
        item["text"]
        for item in value["segments"]
        if isinstance(item, dict) and isinstance(item.get("text"), str)
    )
