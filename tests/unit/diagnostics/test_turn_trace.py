from __future__ import annotations

import asyncio
import json
import socket
import ssl
import urllib.error
from collections.abc import Mapping
from typing import cast

import pytest

from cardine.diagnostics.turn_trace import MAX_OPERATIONS_PER_TRACE, TurnTraceStore, trace_operation
from study_agent.adapters.model import OpenAIGpt56LunaConfig, OpenAIGpt56LunaModel
from study_agent.adapters.model.openai_compatible import HttpResponse
from study_agent.domain._validation import JsonObject
from study_agent.ports.model import (
    MessageRole,
    ModelError,
    ModelErrorCode,
    ModelMessage,
    ModelRequest,
)


def latest(store: TurnTraceStore) -> JsonObject:
    return cast(tuple[JsonObject, ...], store.snapshot()["turn_traces"])[-1]


def operations(trace: JsonObject) -> tuple[JsonObject, ...]:
    return cast(tuple[JsonObject, ...], trace["operations"])


def test_failed_turn_without_a_decision_is_visible_and_payload_free() -> None:
    store = TurnTraceStore()
    with pytest.raises(RuntimeError), store.capture("private-request", 7):
        raise RuntimeError("secret-key private prompt provider body")

    snapshot = store.snapshot()
    assert snapshot["latest_trace_id"] == store.trace_id_for_request("private-request")
    trace = latest(store)
    assert trace["decision"] is None
    assert trace["status"] == "failed"
    assert operations(trace)[-1]["phase"] == "application_turn"
    assert operations(trace)[-1]["error_kind"] == "internal_error"
    encoded = json.dumps(snapshot)
    for private in ("secret-key", "private prompt", "provider body", "private-request"):
        assert private not in encoded


class Transport:
    def __init__(self, outcome: HttpResponse | Exception) -> None:
        self.outcome = outcome

    def post(
        self, url: str, headers: Mapping[str, str], body: bytes, timeout_seconds: float
    ) -> HttpResponse:
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome


@pytest.mark.parametrize(
    ("error", "kind"),
    [
        (
            urllib.error.URLError(ssl.SSLCertVerificationError("secret certificate details")),
            "tls_certificate",
        ),
        (urllib.error.URLError(socket.gaierror("private hostname")), "dns_error"),
        (TimeoutError("private URL"), "timeout"),
        (ConnectionRefusedError("secret endpoint"), "connection_refused"),
        (RuntimeError("private provider body"), "internal_error"),
    ],
)
def test_luna_transport_preserves_safe_failure_detail_across_worker_thread(
    error: Exception, kind: str
) -> None:
    store = TurnTraceStore()
    model = OpenAIGpt56LunaModel(OpenAIGpt56LunaConfig("secret-key"), transport=Transport(error))
    with pytest.raises(ModelError) as raised, store.capture("private-request", 0):
        asyncio.run(
            model.generate(ModelRequest((ModelMessage(MessageRole.USER, "private prompt"),)))
        )
    # The existing adapter still owns classification; tracing does not remap it.
    expected_code = (
        ModelErrorCode.TIMEOUT if isinstance(error, TimeoutError) else ModelErrorCode.UNAVAILABLE
    )
    assert raised.value.code is expected_code
    records = operations(latest(store))
    assert records[2]["phase"] == "provider_http"
    assert records[2]["error_kind"] == kind
    assert cast(int, records[2]["duration_ms"]) >= 0
    assert records[1]["phase"] == "model_generation"
    assert records[1]["error_code"] == expected_code.value
    assert records[0]["error_code"] == expected_code.value
    encoded = json.dumps(store.snapshot())
    for private in ("secret", "private", "Authorization", "api.openai.com"):
        assert private not in encoded


@pytest.mark.parametrize(
    ("status", "code"),
    [
        (401, ModelErrorCode.AUTHENTICATION),
        (429, ModelErrorCode.RATE_LIMITED),
        (503, ModelErrorCode.UNAVAILABLE),
    ],
)
def test_provider_http_status_is_recorded_without_the_response_body(
    status: int, code: ModelErrorCode
) -> None:
    store = TurnTraceStore()
    model = OpenAIGpt56LunaModel(
        OpenAIGpt56LunaConfig("secret-key"),
        transport=Transport(HttpResponse(status, b"secret response body")),
    )
    with pytest.raises(ModelError) as raised, store.capture("request", 0):
        asyncio.run(
            model.generate(ModelRequest((ModelMessage(MessageRole.USER, "private prompt"),)))
        )
    assert raised.value.code is code
    records = operations(latest(store))
    assert records[2]["http_status"] == status
    assert records[2]["status"] == "failed"
    assert records[1]["error_code"] == code.value
    assert records[0]["error_code"] == code.value
    assert "secret" not in json.dumps(store.snapshot())


def test_same_request_retry_is_one_trace_with_a_separate_attempt() -> None:
    store = TurnTraceStore()
    with pytest.raises(ModelError), store.capture("request", 0), trace_operation("model_decision"):
        raise ModelError(ModelErrorCode.UNAVAILABLE, "secret", retryable=True)
    with store.capture("request", 0):
        pass
    assert len(cast(tuple[JsonObject, ...], store.snapshot()["turn_traces"])) == 1
    trace = latest(store)
    assert trace["attempts"] == 2
    assert trace["status"] == "completed"
    assert [record["attempt"] for record in operations(trace)] == [1, 1, 2]
    assert operations(trace)[1]["error_code"] == "unavailable"


def test_trace_retention_and_operation_retention_are_bounded() -> None:
    store = TurnTraceStore(max_traces=2)
    for request in ("old", "recent", "latest"):
        with store.capture(request, 0):
            for _ in range(MAX_OPERATIONS_PER_TRACE + 3):
                with trace_operation("provider_http"):
                    pass
    traces = cast(tuple[JsonObject, ...], store.snapshot()["turn_traces"])
    assert len(traces) == 2
    assert len(operations(traces[-1])) == MAX_OPERATIONS_PER_TRACE
    assert traces[-1]["omitted_operations"] == 4


def test_concurrent_stores_do_not_mix_turns() -> None:
    first, second = TurnTraceStore(), TurnTraceStore()

    async def run(store: TurnTraceStore, request: str, status: int) -> None:
        with store.capture(request, 0):
            await asyncio.sleep(0)
            with trace_operation("provider_http") as operation:
                operation.observe_http_status(status)

    async def both() -> None:
        await asyncio.gather(run(first, "first", 201), run(second, "second", 503))

    asyncio.run(both())
    assert operations(latest(first))[1]["http_status"] == 201
    assert operations(latest(second))[1]["http_status"] == 503


def test_diagnostic_metadata_cannot_mask_the_original_exception_or_leak_its_name() -> None:
    class SecretException(RuntimeError):
        @property
        def code(self) -> str:
            raise RuntimeError("secret property")

    store = TurnTraceStore()
    error = SecretException("secret body")
    with pytest.raises(SecretException) as raised, store.capture("request", 0):
        raise error
    assert raised.value is error
    assert operations(latest(store))[0]["error_type"] == "Exception"
    assert "SecretException" not in json.dumps(store.snapshot())
    assert "secret" not in json.dumps(store.snapshot())


@pytest.mark.parametrize("outcome", ["failed", "terminated", "budget_exhausted"])
def test_returned_failure_is_not_reported_as_success_and_metadata_is_closed(outcome: str) -> None:
    store = TurnTraceStore()
    with store.capture("request", 0) as trace_id:
        store.record_outcome(trace_id, outcome)
        with trace_operation("capability_start") as operation:
            operation.observe_outcome(outcome, "protocol_error")
            operation.observe_outcome("secret prompt", "secret-key")
            operation.observe_http_status(True)
    trace = latest(store)
    assert trace["status"] == "failed"
    assert operations(trace)[0]["outcome"] == outcome
    assert operations(trace)[1]["error_code"] == "protocol_error"
    assert "http_status" not in operations(trace)[1]
    assert "secret" not in json.dumps(store.snapshot())
