"""Shared capability identity fingerprints with no product/host imports."""

from __future__ import annotations

import json
from collections.abc import Mapping
from hashlib import sha256

from study_agent.domain._validation import JsonObject, JsonValue, freeze_object


def capability_retry_fingerprint(idempotency_key: str) -> str:
    if not isinstance(idempotency_key, str) or not idempotency_key.strip():
        raise ValueError("capability idempotency key must be non-blank text")
    return _fingerprint(
        "study-agent-capability-retry-v1",
        {"idempotency_key": idempotency_key},
    )


def capability_output_fingerprint(value: JsonValue) -> str:
    if not isinstance(value, Mapping):
        raise ValueError("completed capability output must be an object")
    return sha256(
        b"study-agent-capability-output-v1\0"
        + _canonical_json_bytes(freeze_object(value))
    ).hexdigest()


def _fingerprint(domain: str, value: JsonObject) -> str:
    return sha256(
        domain.encode("utf-8") + b"\0" + _canonical_json_bytes(value)
    ).hexdigest()


def _canonical_json_bytes(value: JsonValue) -> bytes:
    return json.dumps(
        _plain(value), sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def _plain(value: JsonValue) -> object:
    if isinstance(value, Mapping):
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain(item) for item in value]
    return value


__all__ = ["capability_output_fingerprint", "capability_retry_fingerprint"]
