"""Cardine material lineage and generated-source admission contracts."""

from .generation_contracts import (
    GenerationPipelinePins,
    MaterialGenerationErrorCode,
    MaterialGenerationRequest,
    MaterialGenerationStage,
    MaterialGenerationState,
    MaterialGenerationView,
    PinnedTranscriptInput,
    StageReceipt,
)
from .generation_service import (
    MaterialGenerationConflict,
    MaterialGenerationService,
    MaterialGenerationStale,
)
from .materializer import (
    GeneratedSourceMaterializationError,
    GeneratedSourceMaterializationResult,
    GeneratedSourceMaterializer,
)
from .planning import (
    SegmentBoundaries,
    SegmentBoundary,
    TranscriptUnit,
    UnitManifest,
    build_unit_manifest,
    parse_segment_boundaries,
)
from .verified_batch import MaterialVerifiedBatchAdapter, VerifiedBatchError

__all__ = [
    "GeneratedSourceMaterializationError",
    "GeneratedSourceMaterializationResult",
    "GeneratedSourceMaterializer",
    "GenerationPipelinePins",
    "MaterialGenerationConflict",
    "MaterialGenerationErrorCode",
    "MaterialGenerationRequest",
    "MaterialGenerationService",
    "MaterialGenerationStage",
    "MaterialGenerationStale",
    "MaterialGenerationState",
    "MaterialGenerationView",
    "MaterialVerifiedBatchAdapter",
    "PinnedTranscriptInput",
    "SegmentBoundaries",
    "SegmentBoundary",
    "StageReceipt",
    "TranscriptUnit",
    "UnitManifest",
    "VerifiedBatchError",
    "build_unit_manifest",
    "parse_segment_boundaries",
]
