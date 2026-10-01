"""Immutable revision material to a provider-neutral derived index."""

from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
from typing import Protocol

from study_agent.domain._validation import JsonObject, freeze_object, require_text
from study_agent.domain.document_index import DocumentIndex
from study_agent.domain.identifiers import RevisionId, SourceId, SubstrateId


@dataclass(frozen=True, slots=True)
class DocumentIndexRequest:
    source_id: SourceId
    revision_id: RevisionId
    substrate_id: SubstrateId
    media_type: str
    content: bytes
    normalized_text: str | None
    metadata: JsonObject = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, cls in (
            ("source_id", SourceId),
            ("revision_id", RevisionId),
            ("substrate_id", SubstrateId),
        ):
            if not isinstance(getattr(self, name), cls):
                raise ValueError(f"{name} must be {cls.__name__}")
        require_text(self.media_type, "media_type")
        if not isinstance(self.content, bytes) or not self.content:
            raise ValueError("index request content must be nonempty immutable bytes")
        if self.normalized_text is not None:
            if not isinstance(self.normalized_text, str) or not self.normalized_text:
                raise ValueError("normalized text must be nonempty text")
            digest = sha256(self.normalized_text.encode("utf-8")).hexdigest()
            if str(self.substrate_id) != f"substrate:sha256:{digest}":
                raise ValueError("normalized text does not match substrate binding")
        object.__setattr__(self, "metadata", freeze_object(self.metadata))


class DocumentIndexPort(Protocol):
    async def build(self, request: DocumentIndexRequest) -> DocumentIndex: ...
