"""One-shot Luna adapter for recovering an empty lexical retrieval query."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from study_agent.domain._validation import JsonObject
from study_agent.ports import (
    MessageRole,
    ModelFinishReason,
    ModelMessage,
    ModelPort,
    ModelRequest,
    StructuredOutputConstraint,
)
from study_agent.prompts.retrieval_query_recovery_v1 import (
    RETRIEVAL_QUERY_RECOVERY_INSTRUCTION,
    RETRIEVAL_QUERY_RECOVERY_PROMPT,
)

_MAX_QUERY_CHARS = 160
_SCHEMA: JsonObject = {
    "type": "object",
    "required": ("queries",),
    "additionalProperties": False,
    "properties": {
        "queries": {
            "type": "array",
            "minItems": 1,
            "maxItems": 3,
            "items": {"type": "string", "minLength": 1, "maxLength": _MAX_QUERY_CHARS},
        }
    },
}


class RetrievalQueryRecovery:
    """Ask the configured model once for bounded alternatives to an empty search."""

    def __init__(self, model: ModelPort) -> None:
        if not model.capabilities.structured_output:
            raise TypeError("query recovery requires a structured-output model")
        self._model = model

    async def alternatives(
        self,
        *,
        learner_request: str,
        failed_query: str,
        source_vocabulary: Sequence[str],
    ) -> tuple[str, ...]:
        payload = json.dumps(
            {
                "learner_request": learner_request,
                "failed_query": failed_query,
                "source_vocabulary": list(source_vocabulary),
            },
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        request = ModelRequest(
            (
                ModelMessage(MessageRole.SYSTEM, RETRIEVAL_QUERY_RECOVERY_INSTRUCTION),
                ModelMessage(MessageRole.USER, payload),
            ),
            StructuredOutputConstraint("retrieval_query_recovery", _SCHEMA, True),
            max_output_tokens=256,
            temperature=0,
            metadata={
                "prompt_id": RETRIEVAL_QUERY_RECOVERY_PROMPT.id,
                "prompt_version": str(RETRIEVAL_QUERY_RECOVERY_PROMPT.version),
            },
        )
        try:
            response = await self._model.generate(request)
        except Exception:
            return ()
        if response.finish_reason is not ModelFinishReason.STOP:
            return ()
        output = response.structured_output
        if not isinstance(output, Mapping) or set(output) != {"queries"}:
            return ()
        raw_queries = output.get("queries")
        if not isinstance(raw_queries, tuple | list) or not 1 <= len(raw_queries) <= 3:
            return ()
        failed = " ".join(failed_query.split()).casefold()
        selected: list[str] = []
        seen: set[str] = {failed}
        for raw_query in raw_queries:
            if not isinstance(raw_query, str):
                return ()
            query = " ".join(raw_query.split())
            folded = query.casefold()
            if not query or len(query) > _MAX_QUERY_CHARS or folded in seen:
                continue
            seen.add(folded)
            selected.append(query)
        return tuple(selected)


__all__ = ["RetrievalQueryRecovery"]
