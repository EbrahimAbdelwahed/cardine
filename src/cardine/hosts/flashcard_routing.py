"""Host-fixed payload schema for the closed Jev flashcard decisions."""
from __future__ import annotations

from collections.abc import Mapping

from cardine.application.flashcard_scope import FlashcardScope
from study_agent.domain._validation import JsonObject


def flashcard_payload_schema(
    schema: JsonObject, scope: FlashcardScope, contextual_query: str | None,
) -> JsonObject:
    properties = schema.get("properties")
    if not isinstance(properties, Mapping) or "scope" not in properties:
        raise ValueError("flashcard payload schema is invalid")
    selected: JsonObject = {**properties,
        "scope": {"type": "string", "enum": (scope.encode(),)},
        "continuation_summary_json": {"type": "null"},
    }
    if contextual_query is not None:
        selected = {**selected, "query": {"type": "string", "enum": (contextual_query,)}}
    return {**schema, "properties": selected}
