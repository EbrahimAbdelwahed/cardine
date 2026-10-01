"""Offline fixtures for the qualified TypeSafe SDK Choice protocol."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any

import pytest

from study_agent.adapters.judgement.jev import JevChoiceAdapter, JevProviderError
from study_agent.ports.judgement import ChoiceJudgementRequest, ChoiceOption


def request() -> ChoiceJudgementRequest:
    return ChoiceJudgementRequest(
        "Choose an option",
        {"excerpt": "bounded private text"},
        (ChoiceOption("a", "First"), ChoiceOption("b", "Second")),
        {"secret_metadata": "do not serialize"},
    )


def payload() -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "usage": {"input_tokens": 12, "output_tokens": 2},
        "answers": {
            "answer": {
                "type": "choice",
                "choice": "a",
                "confidence": 0.85,
                "probabilities": {"a": 0.8, "b": 0.2},
            }
        },
    }


class FakeSDK:
    def __init__(self, raw: bytes | None = None, delay: float = 0) -> None:
        self.raw = raw if raw is not None else json.dumps(payload()).encode()
        self.delay = delay
        self.calls: list[dict[str, Any]] = []
        self.clients: list[dict[str, Any]] = []
        self.active = 0
        self.peak = 0
        self.closed = 0

    @staticmethod
    def Choice(**kwargs: Any) -> object:
        return SimpleNamespace(**kwargs)

    @staticmethod
    def RetryPolicy(**kwargs: Any) -> object:
        return SimpleNamespace(**kwargs)

    def AsyncTypeSafeClient(self, **kwargs: Any) -> object:
        sdk = self
        sdk.clients.append(kwargs)

        class Client:
            async def __aenter__(self) -> Client:
                return self

            async def __aexit__(self, *args: object) -> None:
                sdk.closed += 1

            async def system_one(self, **call: Any) -> object:
                sdk.calls.append(call)
                sdk.active += 1
                sdk.peak = max(sdk.peak, sdk.active)
                try:
                    await asyncio.sleep(sdk.delay)
                    return SimpleNamespace(raw_http_response=SimpleNamespace(content=sdk.raw))
                finally:
                    sdk.active -= 1

        return Client()


def test_sdk_choice_mapping_and_metadata() -> None:
    sdk = FakeSDK()
    adapter = JevChoiceAdapter(api_key="fixture-key", max_retries=3, sdk_loader=lambda: sdk)
    result = asyncio.run(adapter.judge(request()))
    call = sdk.calls[0]
    assert call["state"] == {"excerpt": "bounded private text"}
    assert "secret_metadata" not in call
    assert call["model"] == "jev-latest"
    assert call["questions"]["answer"].instructions == "Choose an option"
    assert call["questions"]["answer"].criteria == {"a": "First", "b": "Second"}
    assert sdk.clients[0]["retry"].max_retries == 3
    assert result.model_id == "jev-1.13.0"
    assert result.selected_key == "a"
    assert [p.key for p in result.probabilities] == ["a", "b"]
    assert result.usage == {"input_tokens": 12, "output_tokens": 2}
    assert result.latency_ms is not None and result.latency_ms >= 0
    assert sdk.closed == 1


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
    ],
)
def test_malformed_result_fails_without_semantic_retry(change: str) -> None:
    raw = payload()
    answer = raw["answers"]["answer"]
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
    sdk = FakeSDK(json.dumps(raw).encode())
    with pytest.raises(JevProviderError, match="jev_malformed_response"):
        asyncio.run(JevChoiceAdapter(sdk_loader=lambda: sdk).judge(request()))
    assert len(sdk.calls) == 1


def test_original_duplicate_probability_keys_rejected() -> None:
    raw = json.dumps(payload()).replace('"a": 0.8', '"a": 0.4, "a": 0.8').encode()
    sdk = FakeSDK(raw)
    with pytest.raises(JevProviderError, match="jev_malformed_response"):
        asyncio.run(JevChoiceAdapter(sdk_loader=lambda: sdk).judge(request()))


def test_small_rounding_error_normalized_in_request_order() -> None:
    raw = payload()
    raw["answers"]["answer"]["probabilities"] = {"b": 0.2000001, "a": 0.8}
    sdk = FakeSDK(json.dumps(raw).encode())
    result = asyncio.run(JevChoiceAdapter(sdk_loader=lambda: sdk).judge(request()))
    assert sum(p.probability for p in result.probabilities) == pytest.approx(1)
    assert [p.key for p in result.probabilities] == ["a", "b"]


def test_shared_concurrency_across_adapter_instances() -> None:
    sdk = FakeSDK(delay=0.01)
    adapters = [JevChoiceAdapter(concurrency=2, sdk_loader=lambda: sdk) for _ in range(3)]

    async def run() -> None:
        results = await asyncio.gather(*(adapters[i % 3].judge(request()) for i in range(12)))
        assert len(results) == 12

    asyncio.run(run())
    assert sdk.peak == 2
    assert len(sdk.calls) == sdk.closed == 12


def test_timeout_cancels_transport_and_closes_client() -> None:
    sdk = FakeSDK(delay=1)
    adapter = JevChoiceAdapter(timeout_seconds=0.01, sdk_loader=lambda: sdk)
    with pytest.raises(JevProviderError, match="jev_timeout"):
        asyncio.run(adapter.judge(request()))
    assert sdk.active == 0
    assert sdk.closed == 1
    assert len(sdk.calls) == 1


def test_unavailable_sdk_is_explicit() -> None:
    def missing() -> Any:
        raise ModuleNotFoundError("private text and credentials")

    with pytest.raises(JevProviderError) as caught:
        asyncio.run(JevChoiceAdapter(sdk_loader=missing).judge(request()))
    assert str(caught.value) == "jev_sdk_unavailable"
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("status, expected_attempts", [(429, 2), (503, 2), (400, 1), (401, 1)])
def test_official_sdk_http_contract_and_transport_only_retry(
    status: int,
    expected_attempts: int,
) -> None:
    sdk = pytest.importorskip("typesafe_sdk")
    httpx = pytest.importorskip("httpx2")
    bodies: list[dict[str, Any]] = []

    def handler(incoming: Any) -> Any:
        bodies.append(json.loads(incoming.content))
        assert str(incoming.url) == "https://api.typesafe.ai/v1/systemone"
        if len(bodies) == 1:
            return httpx.Response(
                status, json={"error": "private-source-and-key"}, headers={"retry-after-ms": "0"}
            )
        return httpx.Response(200, json=payload())

    def client(**kwargs: Any) -> Any:
        return sdk.AsyncTypeSafeClient(**kwargs, transport=httpx.MockTransport(handler))

    facade = SimpleNamespace(
        Choice=sdk.Choice,
        RetryPolicy=sdk.RetryPolicy,
        AsyncTypeSafeClient=client,
    )
    adapter = JevChoiceAdapter(api_key="fixture-key", max_retries=1, sdk_loader=lambda: facade)
    if expected_attempts == 2:
        result = asyncio.run(adapter.judge(request()))
        assert result.selected_key == "a"
    else:
        with pytest.raises(JevProviderError) as caught:
            asyncio.run(adapter.judge(request()))
        assert str(caught.value) == "jev_provider_failure"
        assert caught.value.__cause__ is None
    assert len(bodies) == expected_attempts
    assert bodies[-1] == {
        "state": {"excerpt": "bounded private text"},
        "model": "jev-latest",
        "questions": {
            "answer": {
                "type": "choice",
                "instructions": "Choose an option",
                "criteria": {"a": "First", "b": "Second"},
            }
        },
    }
