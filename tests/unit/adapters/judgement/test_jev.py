"""Offline native HTTP fixtures for OpenRouter's alpha Decisions contract."""

from __future__ import annotations

import asyncio
import importlib
import json
from typing import Any

import pytest

from study_agent.adapters.judgement.jev import JevChoiceAdapter, JevProviderError
from study_agent.ports.judgement import ChoiceJudgementRequest, ChoiceOption


def http() -> Any:
    return pytest.importorskip("httpx")


def request() -> ChoiceJudgementRequest:
    return ChoiceJudgementRequest(
        "Choose an option",
        {"excerpt": "bounded private text", "items": (1, 2)},
        (ChoiceOption("a", "First"), ChoiceOption("b", "Second")),
        {"secret_metadata": "do not serialize"},
    )


def payload() -> dict[str, Any]:
    return {
        "model": "typesafe/jev-1.13-20260917",
        "usage": {"input_tokens": 12, "output_tokens": 2, "cost": 0.000002},
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "a",
                "confidence": 0.85,
                "probabilities": {"a": 0.8, "b": 0.2},
            }
        },
    }


def adapter(handler: Any, **kwargs: Any) -> JevChoiceAdapter:
    return JevChoiceAdapter(
        api_key="fixture-key",
        transport=http().MockTransport(handler),
        retry_backoff_seconds=0,
        **kwargs,
    )


def test_native_choice_wire_mapping_authentication_and_metadata() -> None:
    calls = []

    def handler(incoming: Any) -> Any:
        calls.append(incoming)
        return http().Response(200, json=payload())

    result = asyncio.run(adapter(handler).judge(request()))
    wire = calls[0]
    assert str(wire.url) == "https://openrouter.ai/api/alpha/decisions"
    assert wire.method == "POST"
    assert wire.headers["authorization"] == "Bearer fixture-key"
    assert wire.headers["content-type"] == "application/json"
    assert json.loads(wire.content) == {
        "state": {"excerpt": "bounded private text", "items": [1, 2]},
        "model": "typesafe/jev-1.13",
        "questions": {
            "decision": {
                "type": "choice",
                "instructions": "Choose an option",
                "criteria": {"a": "First", "b": "Second"},
            }
        },
    }
    assert result.model_id == "typesafe/jev-1.13-20260917"
    assert result.producer_id == "openrouter-jev"
    assert result.producer_version == "openrouter-decisions-alpha@1"
    assert result.selected_key == "a"
    assert [p.key for p in result.probabilities] == ["a", "b"]
    assert result.usage == {"input_tokens": 12, "output_tokens": 2, "cost": 0.000002}
    assert result.latency_ms is not None and result.latency_ms >= 0


@pytest.mark.parametrize(
    "change",
    [
        "missing",
        "unknown",
        "selected",
        "negative",
        "nan",
        "infinite",
        "zero",
        "unnormalized",
        "boolean",
        "confidence",
        "answer_kind",
        "extra_answer",
        "model",
        "missing_model",
        "usage_type",
        "token_type",
        "cost",
        "missing_usage",
    ],
)
def test_malformed_result_fails_without_semantic_retry(change: str) -> None:
    raw = payload()
    answer = raw["answers"]["decision"]
    if change == "missing":
        del answer["probabilities"]["b"]
    elif change == "unknown":
        answer["probabilities"]["c"] = 0.1
    elif change == "selected":
        answer["choice"] = "unknown"
    elif change == "negative":
        answer["probabilities"] = {"a": 1.1, "b": -0.1}
    elif change == "nan":
        answer["probabilities"]["a"] = float("nan")
    elif change == "infinite":
        answer["probabilities"]["a"] = float("inf")
    elif change == "zero":
        answer["probabilities"] = {"a": 0, "b": 0}
    elif change == "unnormalized":
        answer["probabilities"] = {"a": 0.8, "b": 0.1}
    elif change == "boolean":
        answer["probabilities"]["a"] = True
    elif change == "confidence":
        answer["confidence"] = 1.1
    elif change == "answer_kind":
        answer["type"] = "noul"
    elif change == "extra_answer":
        raw["answers"]["other"] = answer
    elif change == "model":
        raw["model"] = ""
    elif change == "missing_model":
        del raw["model"]
    elif change == "usage_type":
        raw["usage"] = "private"
    elif change == "token_type":
        raw["usage"]["input_tokens"] = True
    elif change == "cost":
        raw["usage"]["cost"] = float("nan")
    elif change == "missing_usage":
        del raw["usage"]
    calls = []

    def handler(incoming: Any) -> Any:
        calls.append(incoming)
        return http().Response(200, content=json.dumps(raw).encode())

    with pytest.raises(JevProviderError, match="jev_malformed_response"):
        asyncio.run(adapter(handler).judge(request()))
    assert len(calls) == 1


@pytest.mark.parametrize(
    "raw",
    [
        json.dumps(payload()).replace('"a": 0.8', '"a": 0.4, "a": 0.8').encode(),
        b"not-json private-source-and-key",
        b"[]",
        b"null",
        b"{",
    ],
)
def test_original_duplicate_keys_and_invalid_json_rejected(raw: bytes) -> None:
    with pytest.raises(JevProviderError, match="jev_malformed_response"):
        asyncio.run(adapter(lambda _: http().Response(200, content=raw)).judge(request()))


def test_small_rounding_error_normalized_in_request_order() -> None:
    raw = payload()
    raw["answers"]["decision"]["probabilities"] = {"b": 0.2000001, "a": 0.8}
    result = asyncio.run(adapter(lambda _: http().Response(200, json=raw)).judge(request()))
    assert sum(p.probability for p in result.probabilities) == pytest.approx(1)
    assert [p.key for p in result.probabilities] == ["a", "b"]


@pytest.mark.parametrize(
    "status, attempts",
    [
        (408, 2),
        (429, 2),
        (500, 2),
        (503, 2),
        (524, 2),
        (529, 2),
        (302, 1),
        (400, 1),
        (401, 1),
        (402, 1),
        (403, 1),
        (413, 1),
    ],
)
def test_http_transport_only_retry_and_no_redirect(status: int, attempts: int) -> None:
    codes = {401: "jev_credentials_rejected", 403: "jev_credentials_rejected",
             402: "jev_credits_exhausted", 400: "jev_request_rejected",
             413: "jev_request_rejected"}
    calls = []

    def handler(incoming: Any) -> Any:
        calls.append(incoming)
        if len(calls) == 1:
            return http().Response(
                status,
                json={"error": "private-source-and-key"},
                headers={"Retry-After": "0", "Location": "https://elsewhere.invalid"},
            )
        return http().Response(200, json=payload())

    if attempts == 2:
        assert asyncio.run(adapter(handler, max_retries=1).judge(request())).selected_key == "a"
    else:
        with pytest.raises(JevProviderError) as caught:
            asyncio.run(adapter(handler, max_retries=1).judge(request()))
        assert str(caught.value) == codes.get(status, "jev_provider_failure")
        assert caught.value.code == str(caught.value)
        assert caught.value.__cause__ is None
    assert len(calls) == attempts


@pytest.mark.parametrize("error_name", ["ConnectError", "ReadError", "ReadTimeout"])
def test_native_transport_errors_retry_within_one_budget(error_name: str) -> None:
    calls = []

    def handler(incoming: Any) -> Any:
        calls.append(incoming)
        if len(calls) == 1:
            raise getattr(http(), error_name)("private-source-and-key", request=incoming)
        return http().Response(200, json=payload())

    assert asyncio.run(adapter(handler, max_retries=1).judge(request())).selected_key == "a"
    assert len(calls) == 2


def test_retry_exhaustion_sanitizes_error_and_limits_attempts() -> None:
    calls = []

    def handler(incoming: Any) -> Any:
        calls.append(incoming)
        raise http().ConnectError("private-source-and-key", request=incoming)

    with pytest.raises(JevProviderError, match="jev_provider_failure"):
        asyncio.run(adapter(handler, max_retries=2).judge(request()))
    assert len(calls) == 3


def test_shared_concurrency_across_adapter_instances() -> None:
    active = 0
    peak = 0
    calls = 0

    async def handler(incoming: Any) -> Any:
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        try:
            await asyncio.sleep(0.01)
            return http().Response(200, json=payload())
        finally:
            active -= 1

    adapters = [adapter(handler, concurrency=2) for _ in range(3)]

    async def run() -> None:
        results = await asyncio.gather(*(adapters[i % 3].judge(request()) for i in range(12)))
        assert len(results) == 12

    asyncio.run(run())
    assert peak == 2
    assert calls == 12
    assert active == 0


def test_timeout_cancels_native_transport() -> None:
    cancelled = False
    calls = 0

    async def handler(incoming: Any) -> Any:
        nonlocal cancelled, calls
        calls += 1
        try:
            await asyncio.sleep(1)
        except asyncio.CancelledError:
            cancelled = True
            raise
        return http().Response(200, json=payload())

    with pytest.raises(JevProviderError, match="jev_timeout"):
        asyncio.run(adapter(handler, timeout_seconds=0.01).judge(request()))
    assert calls == 1
    assert cancelled


def test_timeout_includes_shared_budget_queue() -> None:
    started = asyncio.Event()
    released = asyncio.Event()
    calls = 0

    async def handler(incoming: Any) -> Any:
        nonlocal calls
        calls += 1
        started.set()
        await released.wait()
        return http().Response(200, json=payload())

    async def run() -> None:
        first = asyncio.create_task(adapter(handler, concurrency=1).judge(request()))
        await started.wait()
        with pytest.raises(JevProviderError, match="jev_timeout"):
            await adapter(handler, concurrency=1, timeout_seconds=0.01).judge(request())
        released.set()
        await first

    asyncio.run(run())
    assert calls == 1


def test_retry_after_delay_is_inside_whole_operation_deadline() -> None:
    calls = 0

    def handler(incoming: Any) -> Any:
        nonlocal calls
        calls += 1
        return http().Response(429, headers={"Retry-After": "60"})

    with pytest.raises(JevProviderError, match="jev_timeout"):
        asyncio.run(adapter(handler, timeout_seconds=0.01).judge(request()))
    assert calls == 1


def test_credentials_come_from_openrouter_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENROUTER_API_KEY", "env-fixture-key")
    calls = []

    def handler(incoming: Any) -> Any:
        calls.append(incoming)
        return http().Response(200, json=payload())

    value = JevChoiceAdapter(transport=http().MockTransport(handler))
    asyncio.run(value.judge(request()))
    assert calls[0].headers["authorization"] == "Bearer env-fixture-key"


@pytest.mark.parametrize("key", [None, "", "   ", "secret\nkey", "secret key", "☃"])
def test_missing_invalid_credentials_fail_without_transport(
    key: str | None,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "never-read-this-key")
    with pytest.raises(JevProviderError, match="jev_credentials_unavailable"):
        asyncio.run(JevChoiceAdapter(api_key=key).judge(request()))


def test_optional_http_dependency_unavailable_is_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    original = importlib.import_module

    def missing(name: str, *args: object, **kwargs: object) -> Any:
        if name == "httpx":
            raise ModuleNotFoundError("private-source-and-key")
        return original(name)

    monkeypatch.setattr(importlib, "import_module", missing)
    with pytest.raises(JevProviderError, match="jev_http_unavailable") as caught:
        asyncio.run(JevChoiceAdapter(api_key="fixture-key").judge(request()))
    assert caught.value.__cause__ is None
