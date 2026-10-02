from __future__ import annotations

import json
from collections.abc import Mapping
from typing import cast

import pytest

from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.document_index import DocumentIndex
from study_agent.state.serialization import canonical_json_bytes, canonical_json_object
from tests.unit.knowledge.test_document_index import index, node


def test_index_cache_roundtrip_is_byte_stable_and_identity_preserving() -> None:
    original = index(
        (node(children=("child",)), node("child", parent="root", order=1, start=5, end=12))
    )
    encoded = canonical_json_bytes(original.to_json())
    restored = DocumentIndex.from_json(canonical_json_object(encoded))
    assert restored == original
    assert canonical_json_bytes(restored.to_json()) == encoded
    assert DocumentIndex.from_json(cast(JsonObject, json.loads(encoded))) == original


def test_cached_index_tampering_and_schema_expansion_rejected() -> None:
    original = index()
    payload: dict[str, JsonValue] = dict(original.to_json())
    payload["producer_version"] = "changed"
    with pytest.raises(ValueError, match="fingerprint"):
        DocumentIndex.from_json(payload)
    payload = dict(original.to_json())
    payload["fingerprint"] = ""
    with pytest.raises(ValueError, match="fingerprint"):
        DocumentIndex.from_json(payload)
    payload = dict(original.to_json())
    payload["provider_raw_text"] = "must not enter derived contract"
    with pytest.raises(ValueError, match="fields"):
        DocumentIndex.from_json(payload)


def test_cached_locator_types_and_unknown_fields_are_strict() -> None:
    payload = dict(index().to_json())
    raw_node = dict(cast(Mapping[str, JsonValue], cast(tuple[JsonValue, ...], payload["nodes"])[0]))
    raw_locator = dict(cast(Mapping[str, JsonValue], raw_node["locator"]))
    raw_locator["start_offset"] = True
    raw_node["locator"] = raw_locator
    payload["nodes"] = (raw_node,)
    with pytest.raises(ValueError, match="integers"):
        DocumentIndex.from_json(payload)
    raw_locator["start_offset"] = 0
    raw_locator["unknown"] = 0
    with pytest.raises(ValueError, match="fields"):
        DocumentIndex.from_json(payload)
