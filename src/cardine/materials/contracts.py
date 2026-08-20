"""Small public contracts for generated-source materialization."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from study_agent.domain.source import SourceChunk, SourceDocument


class GeneratedSourceMaterializationStatus(StrEnum):
    EMITTED = "emitted"
    IDEMPOTENT = "idempotent"


@dataclass(frozen=True, slots=True)
class GeneratedSourceMaterializationResult:
    status: GeneratedSourceMaterializationStatus
    source: SourceDocument
    chunks: tuple[SourceChunk, ...]
    committed_sequence: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "chunks", tuple(self.chunks))


__all__ = [
    "GeneratedSourceMaterializationResult",
    "GeneratedSourceMaterializationStatus",
]
