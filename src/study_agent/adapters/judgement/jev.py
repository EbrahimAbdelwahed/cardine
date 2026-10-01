"""TypeSafe System One Choice transport, independent of consumer policy.

Protocol qualified against typesafe-sdk 0.7.2. SDK imports and raw responses
stay inside this module; importing Cardine's core needs no provider extras.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import logging
import math
import os
from collections.abc import Callable, Mapping
from time import perf_counter
from typing import Any
from weakref import WeakKeyDictionary

from study_agent.ports.judgement import (
    ChoiceJudgement,
    ChoiceJudgementRequest,
    ChoiceProbability,
    validate_judgement,
)

_BUDGETS: WeakKeyDictionary[asyncio.AbstractEventLoop, tuple[int, asyncio.Semaphore]] = (
    WeakKeyDictionary()
)
_NORMALIZATION_TOLERANCE = 1e-6


class JevProviderError(RuntimeError):
    """Sanitized provider failure safe for receipts (never contains payloads)."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def _shared_budget(limit: int) -> asyncio.Semaphore:
    loop = asyncio.get_running_loop()
    existing = _BUDGETS.get(loop)
    if existing is None:
        semaphore = asyncio.Semaphore(limit)
        _BUDGETS[loop] = (limit, semaphore)
        return semaphore
    if existing[0] != limit:
        raise JevProviderError("jev_concurrency_conflict")
    return existing[1]


def _load_sdk() -> Any:
    return importlib.import_module("typesafe_sdk")


class JevChoiceAdapter:
    """One Choice adapter for all consumers, sharing a per-loop request budget.

    ``timeout_seconds`` bounds the complete judgement including queueing and
    SDK transport retries. Cancellation reaches native async HTTP operations.
    The SDK owns transport retries; malformed semantic results are never retried.
    Injecting ``sdk_loader`` supports offline SDK-contract fixtures.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str = "jev-latest",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        concurrency: int | None = None,
        sdk_loader: Callable[[], Any] = _load_sdk,
    ) -> None:
        if not model_id or model_id != model_id.strip():
            raise ValueError("model_id must be non-empty trimmed text")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between zero and ten")
        try:
            limit = (
                int(os.environ.get("JEV_CONCURRENCY", "64")) if concurrency is None else concurrency
            )
        except ValueError:
            raise ValueError("JEV_CONCURRENCY must be an integer") from None
        if type(limit) is not int or not 1 <= limit <= 1024:
            raise ValueError("concurrency must be between one and 1024")
        self._api_key = api_key
        self._model_id = model_id
        self._timeout = timeout_seconds
        self._retries = max_retries
        self._concurrency = limit
        self._sdk_loader = sdk_loader

    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
        started = perf_counter()
        try:
            sdk = self._sdk_loader()
        except ImportError:
            raise JevProviderError("jev_sdk_unavailable") from None
        # The SDK's DEBUG logger includes request bodies. Disable its output at
        # this provider boundary so source text cannot leak through app logging.
        logging.getLogger("typesafe_sdk").disabled = True
        try:
            question = sdk.Choice(
                instructions=request.instruction,
                criteria={option.key: option.description for option in request.options},
            )
            retry = sdk.RetryPolicy(max_retries=self._retries, timeout=self._timeout)
            async with asyncio.timeout(self._timeout):
                async with _shared_budget(self._concurrency):
                    async with sdk.AsyncTypeSafeClient(
                        api_key=self._api_key,
                        model=self._model_id,
                        retry=retry,
                        timeout=self._timeout,
                    ) as client:
                        response = await client.system_one(
                            state=_plain_json(request.state),
                            questions={"answer": question},
                            model=self._model_id,
                        )
            return _normalize_response(response, request, (perf_counter() - started) * 1000)
        except JevProviderError:
            raise
        except TimeoutError:
            raise JevProviderError("jev_timeout") from None
        except Exception:
            raise JevProviderError("jev_provider_failure") from None


def _plain_json(value: object) -> Any:
    if isinstance(value, Mapping):
        return {key: _plain_json(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain_json(item) for item in value]
    return value


def _unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def _normalize_response(
    response: Any, request: ChoiceJudgementRequest, latency_ms: float
) -> ChoiceJudgement:
    try:
        # Validate original JSON before using SDK maps: dict decoding can hide
        # duplicate probability keys. Never expose or persist the raw payload.
        raw = json.loads(response.raw_http_response.content, object_pairs_hook=_unique_pairs)
        answer = raw["answers"]["answer"]
        if set(raw["answers"]) != {"answer"} or answer["type"] != "choice":
            raise ValueError("answer set")
        selected = answer["choice"]
        probabilities = answer["probabilities"]
        keys = tuple(option.key for option in request.options)
        if not isinstance(probabilities, dict) or set(probabilities) != set(keys):
            raise ValueError("probability keys")
        values = tuple(probabilities[key] for key in keys)
        if any(type(value) not in {int, float} for value in values):
            raise ValueError("probability type")
        if any(not math.isfinite(value) or value < 0 or value > 1 for value in values):
            raise ValueError("probability bounds")
        total = math.fsum(values)
        if abs(total - 1.0) > _NORMALIZATION_TOLERANCE:
            raise ValueError("normalization")
        confidence = answer["confidence"]
        if type(confidence) not in {int, float} or not math.isfinite(confidence):
            raise ValueError("confidence")
        usage = raw.get("usage", {})
        normalized_usage = {}
        for name in ("input_tokens", "output_tokens"):
            value = usage.get(name)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise ValueError("usage")
                normalized_usage[name] = value
        result = ChoiceJudgement(
            selected_key=selected,
            probabilities=tuple(
                ChoiceProbability(key, value / total)
                for key, value in zip(keys, values, strict=True)
            ),
            confidence=confidence,
            producer_id="typesafe-jev",
            producer_version="typesafe-sdk-0.7.2",
            model_id=raw["model"],
            latency_ms=latency_ms,
            usage=normalized_usage,
        )
        return validate_judgement(request, result)
    except (AttributeError, KeyError, TypeError, ValueError):
        raise JevProviderError("jev_malformed_response") from None
