from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from cardine.adapters.model.streaming import HttpxStreamingTransport, draft_text
from study_agent.ports.model import ModelError, ModelErrorCode


def test_native_sse_yields_before_body_completion_and_closes_on_cancellation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    http = pytest.importorskip("httpx")
    client = http.AsyncClient
    visited: list[int] = []
    closed: list[bool] = []

    class Stream(http.AsyncByteStream):  # type: ignore[misc, name-defined]
        async def __aiter__(self) -> AsyncIterator[bytes]:
            visited.append(1)
            yield ': keepalive\ndata: {"text":"cuore è"}\n\n'.encode()
            visited.append(2)
            yield b"data: [DONE]\n\n"

        async def aclose(self) -> None:
            closed.append(True)

    def handler(request: Any) -> Any:
        assert request.method == "POST"
        assert request.content == b"{}"
        return http.Response(200, stream=Stream())

    def factory(**kwargs: Any) -> Any:
        return client(transport=http.MockTransport(handler), **kwargs)

    monkeypatch.setattr(http, "AsyncClient", factory)

    async def consume() -> None:
        events = HttpxStreamingTransport().events("https://offline.invalid/", {}, b"{}", 1)
        try:
            assert await anext(events) == '{"text":"cuore è"}'
            assert visited == [1], "transport buffered the entire response"
        finally:
            await events.aclose()

    asyncio.run(consume())
    assert closed == [True]


@pytest.mark.parametrize("failure", ["timeout", "status"])
def test_native_sse_errors_are_typed_and_do_not_leak_payloads(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    http = pytest.importorskip("httpx")
    client = http.AsyncClient

    def handler(request: Any) -> Any:
        if failure == "timeout":
            raise http.ReadTimeout("private-provider-payload", request=request)
        return http.Response(503, text="private-provider-payload")

    def factory(**kwargs: Any) -> Any:
        return client(transport=http.MockTransport(handler), **kwargs)

    monkeypatch.setattr(http, "AsyncClient", factory)

    async def consume() -> None:
        async for _ in HttpxStreamingTransport().events("https://offline.invalid/", {}, b"{}", 1):
            pass

    with pytest.raises(ModelError) as caught:
        asyncio.run(consume())
    assert caught.value.code is (
        ModelErrorCode.TIMEOUT if failure == "timeout" else ModelErrorCode.UNAVAILABLE
    )
    assert "private-provider-payload" not in str(caught.value)


def test_partial_json_exposes_only_answer_text_and_decodes_escapes() -> None:
    pytest.importorskip("pydantic_core")
    assert draft_text('{"segments":[{"text":"cuore \\u00e8') == "cuore è"
    assert draft_text('{"evidence_ids":["private-id"],"tool_arguments":{"text":"private"}}') == ""
    assert draft_text("not-json") == ""


@pytest.mark.parametrize(
    "provider_code,expected",
    [
        ("invalid_json_schema", ModelErrorCode.SCHEMA_INCOMPATIBLE),
        ("unsupported_parameter", ModelErrorCode.ENDPOINT_INCOMPATIBLE),
        ("model_not_found", ModelErrorCode.MODEL_UNAVAILABLE),
    ],
)
def test_streamed_provider_rejection_preserves_error_classification(
    monkeypatch: pytest.MonkeyPatch,
    provider_code: str,
    expected: ModelErrorCode,
) -> None:
    http = pytest.importorskip("httpx")
    client = http.AsyncClient

    def factory(**kwargs: Any) -> Any:
        return client(
            transport=http.MockTransport(
                lambda _request: http.Response(
                    400,
                    json={"error": {"code": provider_code, "message": "private-provider-payload"}},
                )
            ),
            **kwargs,
        )

    monkeypatch.setattr(http, "AsyncClient", factory)

    async def consume() -> None:
        async for _ in HttpxStreamingTransport().events("https://offline.invalid/", {}, b"{}", 1):
            pass

    with pytest.raises(ModelError) as caught:
        asyncio.run(consume())
    assert caught.value.code is expected
    assert "private-provider-payload" not in str(caught.value)


def test_streamed_error_body_is_bounded_and_not_drained(monkeypatch: pytest.MonkeyPatch) -> None:
    http = pytest.importorskip("httpx")
    client = http.AsyncClient

    class ErrorStream(http.AsyncByteStream):  # type: ignore[misc, name-defined]
        async def __aiter__(self) -> AsyncIterator[bytes]:
            yield b"private" * 10_000
            raise AssertionError("oversized error was drained")

    def factory(**kwargs: Any) -> Any:
        return client(
            transport=http.MockTransport(lambda _request: http.Response(400, stream=ErrorStream())),
            **kwargs,
        )

    monkeypatch.setattr(http, "AsyncClient", factory)

    async def consume() -> None:
        async for _ in HttpxStreamingTransport().events("https://offline.invalid/", {}, b"{}", 1):
            pass

    with pytest.raises(ModelError) as caught:
        asyncio.run(consume())
    assert caught.value.code is ModelErrorCode.PROTOCOL_ERROR
    assert "private" not in str(caught.value)
