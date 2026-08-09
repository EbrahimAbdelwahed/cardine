"""Canonical normalization for the Cardine adoption parity corpus.

Parity vectors compare semantics produced by two runtimes.  The only values
that are allowed to vary between runs are clock readings, temporary paths, and
process identifiers.  Keeping the allowlist here (rather than recursively
scrubbing values by shape) prevents a normalizer from hiding a semantic drift.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final

from study_agent.domain._validation import JsonValue

# These names are intentionally narrow.  In particular, ``path`` and
# ``timestamp`` are not treated as wildcards: source paths and event timestamps
# are semantic unless the producer explicitly labels them as temporary/clock
# metadata.
NONDETERMINISTIC_FIELDS: Final[frozenset[str]] = frozenset(
    {
        "clock_now",
        "current_time",
        "occurred_at",
        "created_at",
        "updated_at",
        "started_at",
        "finished_at",
        "expires_at",
        "temporary_path",
        "temp_path",
        "tmp_path",
        "process_id",
        "pid",
    }
)


def normalize_parity(value: JsonValue) -> JsonValue:
    """Return a canonical semantic vector with only nondeterminism removed.

    Mapping keys are compared exactly and sequences retain their order.  This
    is deliberately not a generic redactor: semantic identifiers, event and
    schema versions, sequence/causation/correlation, provenance, citations,
    decisions, status, and safe error codes are all ordinary values and remain
    in the result.
    """

    if isinstance(value, Mapping):
        normalized: dict[str, JsonValue] = {}
        for raw_key, raw_value in value.items():
            key = str(raw_key)
            if key in NONDETERMINISTIC_FIELDS:
                continue
            normalized[key] = normalize_parity(raw_value)
        return normalized
    if isinstance(value, tuple):
        return tuple(normalize_parity(item) for item in value)
    if isinstance(value, list):
        return tuple(normalize_parity(item) for item in value)
    return value


def normalized_json_bytes(value: JsonValue) -> bytes:
    """Encode a normalized value with the repository's canonical JSON codec."""

    from study_agent.state import canonical_json_bytes

    normalized = normalize_parity(value)
    if not isinstance(normalized, Mapping):
        raise TypeError("parity output must be a JSON object")
    return canonical_json_bytes(normalized)


__all__ = ["NONDETERMINISTIC_FIELDS", "normalize_parity", "normalized_json_bytes"]
