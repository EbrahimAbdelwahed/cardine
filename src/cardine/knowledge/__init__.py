"""Cardine-owned navigation contracts over canonical study sources."""

from .lesson_selection import (
    LessonCandidate,
    LessonChunk,
    LessonEvidencePort,
    LessonSearchResult,
    LessonSelectionError,
    LessonSelectionService,
    LessonSource,
    SearchDisposition,
    SourcePin,
)
from .pageindex_projection import (
    CanonicalSpanCandidate,
    PageIndexProjection,
    PageIndexStatus,
    map_structural_tree,
)

__all__ = (
    "CanonicalSpanCandidate",
    "LessonCandidate",
    "LessonChunk",
    "LessonEvidencePort",
    "LessonSearchResult",
    "LessonSelectionError",
    "LessonSelectionService",
    "LessonSource",
    "PageIndexProjection",
    "PageIndexStatus",
    "SearchDisposition",
    "SourcePin",
    "map_structural_tree",
)
