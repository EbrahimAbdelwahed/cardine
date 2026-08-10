"""Safe local views for capability-gap observations."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .contracts import (
    CapabilityGapAggregate,
    CapabilityGapDimensions,
    GapDisposition,
    GapExportState,
    GapKeyV1,
    GapResolutionKind,
    ImpactKind,
    VerificationKind,
)


@dataclass(frozen=True, slots=True)
class CapabilityGapCompactView:
    report_id: str
    gap_key: GapKeyV1
    occurrence_count: int
    disposition: GapDisposition
    local_only: bool = True


@dataclass(frozen=True, slots=True)
class CapabilityGapDetailView:
    gap_key: GapKeyV1
    dimensions: CapabilityGapDimensions
    verification_kind: VerificationKind
    impact_kind: ImpactKind
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    local_only: bool = True
    resolution: GapResolutionKind = GapResolutionKind.UNRESOLVED
    export_state: GapExportState = GapExportState.LOCAL

    @classmethod
    def from_aggregate(cls, aggregate: CapabilityGapAggregate) -> CapabilityGapDetailView:
        return cls(
            gap_key=aggregate.gap_key,
            dimensions=aggregate.dimensions,
            verification_kind=aggregate.verification_kind,
            impact_kind=aggregate.impact_kind,
            first_seen=aggregate.first_seen,
            last_seen=aggregate.last_seen,
            occurrence_count=aggregate.occurrence_count,
            resolution=aggregate.resolution,
            export_state=aggregate.export_state,
        )


__all__ = ["CapabilityGapCompactView", "CapabilityGapDetailView"]
