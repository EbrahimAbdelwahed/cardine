"""OpenRouter Decisions Choice transport, independent of consumer policy.

Protocol: https://openrouter.ai/docs/api/api-reference/alphadecisions/submit-a-decisions-request
The alpha wire contract is explicitly versioned here. Optional async HTTP
imports and raw provider responses stay inside this module.
"""

from __future__ import annotations

import asyncio
import importlib
import json
import math
import os
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
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
_ENDPOINT = "https://openrouter.ai/api/alpha/decisions"
_PROTOCOL_VERSION = "openrouter-decisions-alpha@1"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024


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


class JevChoiceAdapter:
    """Native async OpenRouter Choice with one shared per-loop request budget.

    ``timeout_seconds`` covers queueing, transport attempts, and retry delays.
    Only HTTP transport failures, rate limits, and temporary server failures
    retry. Malformed judgements never retry. ``transport`` is an optional native
    httpx transport for offline fixtures; its lifetime belongs to each call.
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model_id: str = "typesafe/jev-1.13",
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        concurrency: int | None = None,
        retry_backoff_seconds: float = 0.1,
        transport: object | None = None,
    ) -> None:
        if not model_id or model_id != model_id.strip():
            raise ValueError("model_id must be non-empty trimmed text")
        if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be finite and positive")
        if type(max_retries) is not int or not 0 <= max_retries <= 10:
            raise ValueError("max_retries must be between zero and ten")
        if not math.isfinite(retry_backoff_seconds) or not 0 <= retry_backoff_seconds <= 2:
            raise ValueError("retry_backoff_seconds must be between zero and two")
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
        self._backoff = retry_backoff_seconds
        self._transport = transport

    async def judge(self, request: ChoiceJudgementRequest) -> ChoiceJudgement:
        request.__post_init__()
        started = perf_counter()
        api_key = (
            self._api_key if self._api_key is not None else os.environ.get("OPENROUTER_API_KEY")
        )
        if (
            not isinstance(api_key, str)
            or not api_key.strip()
            or not api_key.isascii()
            or any(character.isspace() or ord(character) < 33 for character in api_key.strip())
        ):
            raise JevProviderError("jev_credentials_unavailable")
        try:
            httpx = importlib.import_module("httpx")
        except ImportError:
            raise JevProviderError("jev_http_unavailable") from None
        try:
            async with asyncio.timeout(self._timeout):
                async with _shared_budget(self._concurrency):
                    async with httpx.AsyncClient(
                        timeout=self._timeout,
                        transport=self._transport,
                        follow_redirects=False,
                        trust_env=False,
                    ) as client:
                        raw = await self._send(client, httpx, request, api_key.strip())
                    return _normalize_response(raw, request, (perf_counter() - started) * 1000)
        except JevProviderError:
            raise
        except TimeoutError:
            raise JevProviderError("jev_timeout") from None
        except Exception:
            raise JevProviderError("jev_provider_failure") from None

    async def _send(
        self, client: Any, httpx: Any, request: ChoiceJudgementRequest, api_key: str
    ) -> bytes:
        body = {
            "model": self._model_id,
            "state": _plain_json(request.state),
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": request.instruction,
                    "criteria": {option.key: option.description for option in request.options},
                }
            },
        }
        for attempt in range(self._retries + 1):
            retry_after = None
            try:
                response = await client.post(
                    _ENDPOINT,
                    json=body,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
            except httpx.TransportError as error:
                if attempt == self._retries:
                    code = (
                        "jev_timeout"
                        if isinstance(error, httpx.TimeoutException)
                        else "jev_provider_failure"
                    )
                    raise JevProviderError(code) from None
            else:
                if response.status_code == 200:
                    if len(response.content) > _MAX_RESPONSE_BYTES:
                        raise JevProviderError("jev_malformed_response")
                    return bytes(response.content)
                if (
                    response.status_code not in {408, 429}
                    and not 500 <= response.status_code <= 599
                ):
                    raise JevProviderError("jev_provider_failure")
                if attempt == self._retries:
                    raise JevProviderError("jev_provider_failure")
                retry_after = _retry_delay(response.headers.get("Retry-After"))
            delay = min(2.0, self._backoff * 2**attempt) if retry_after is None else retry_after
            await asyncio.sleep(delay)
        raise JevProviderError("jev_provider_failure")


def _retry_delay(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            when = parsedate_to_datetime(value)
            if when.tzinfo is None:
                when = when.replace(tzinfo=UTC)
            delay = (when - datetime.now(UTC)).total_seconds()
        except (TypeError, ValueError, OverflowError):
            return None
    return max(0.0, delay) if math.isfinite(delay) else None


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
    content: bytes, request: ChoiceJudgementRequest, latency_ms: float
) -> ChoiceJudgement:
    try:
        raw = json.loads(content, object_pairs_hook=_unique_pairs)
        answer = raw["answers"]["decision"]
        if set(raw["answers"]) != {"decision"} or answer["type"] != "choice":
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
        confidence = answer.get("confidence")
        if confidence is not None and (
            type(confidence) not in {int, float} or not math.isfinite(confidence)
        ):
            raise ValueError("confidence")
        usage = raw["usage"]
        if not isinstance(usage, dict):
            raise ValueError("usage")
        normalized_usage = {}
        for name in ("input_tokens", "output_tokens"):
            value = usage.get(name)
            if value is not None:
                if type(value) is not int or value < 0:
                    raise ValueError("usage tokens")
                normalized_usage[name] = value
        cost = usage.get("cost")
        if cost is not None:
            if type(cost) not in {int, float} or not math.isfinite(cost) or cost < 0:
                raise ValueError("usage cost")
            normalized_usage["cost"] = cost
        result = ChoiceJudgement(
            selected_key=selected,
            probabilities=tuple(
                ChoiceProbability(key, value / total)
                for key, value in zip(keys, values, strict=True)
            ),
            confidence=confidence,
            producer_id="openrouter-jev",
            producer_version=_PROTOCOL_VERSION,
            model_id=raw["model"],
            latency_ms=latency_ms,
            usage=normalized_usage,
        )
        return validate_judgement(request, result)
    except (AttributeError, KeyError, TypeError, ValueError, UnicodeDecodeError):
        raise JevProviderError("jev_malformed_response") from None
