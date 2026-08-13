"""Lossless normalization for executable Cardine parity vectors.

The baseline services are allowed to place their temporary SQLite/blob roots in
the response.  Those roots are the only nondeterministic values in the corpus;
all event, identity, timestamp, causation, provenance, citation, decision,
status, and error fields remain part of the comparison contract.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.state import canonical_json_bytes

TMP_POINTERS = frozenset({"/runtime/tmp_root", "/runtime/events_path", "/runtime/blob_root"})
TMP_PLACEHOLDER = "<TMP_ROOT>"


def _pointer(path: tuple[str, ...]) -> str:
    return (
        ""
        if not path
        else "/" + "/".join(item.replace("~", "~0").replace("/", "~1") for item in path)
    )


def normalize_parity(value: JsonValue) -> JsonValue:
    """Replace only the explicitly declared temporary-root JSON pointers."""

    def visit(current: JsonValue, path: tuple[str, ...]) -> JsonValue:
        if _pointer(path) in TMP_POINTERS:
            return TMP_PLACEHOLDER
        if isinstance(current, Mapping):
            return cast(
                JsonObject,
                {str(key): visit(item, (*path, str(key))) for key, item in current.items()},
            )
        if isinstance(current, tuple):
            return tuple(visit(item, (*path, str(index))) for index, item in enumerate(current))
        if isinstance(current, list):
            return tuple(visit(item, (*path, str(index))) for index, item in enumerate(current))
        return current

    return visit(value, ())


def normalized_json_bytes(value: JsonValue) -> bytes:
    normalized = normalize_parity(value)
    if not isinstance(normalized, Mapping):
        raise TypeError("parity vectors must be JSON objects")
    return canonical_json_bytes(normalized)


__all__ = ["TMP_PLACEHOLDER", "TMP_POINTERS", "normalize_parity", "normalized_json_bytes"]
