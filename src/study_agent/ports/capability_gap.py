"""Inward port for the optional capability-gap operational plane."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cardine.feedback.contracts import (
        CapabilityGapObservation,
        CapabilityGapResolution,
        CapabilityGapWriteContext,
        GapExportState,
    )
    from cardine.feedback.host_tool import CapabilityGapHostContext, CapabilityGapProposal
    from cardine.feedback.view import CapabilityGapCompactView


class CapabilityGapStore(Protocol):
    """Store canonical aggregate bytes without owning tutor/course state."""

    def create_or_increment(
        self, gap_key: str, report_id: str, payload: bytes
    ) -> tuple[bytes, bool]: ...

    def create_or_increment_rate_limited(
        self,
        gap_key: str,
        report_id: str,
        payload: bytes,
        *,
        max_occurrences: int,
        window_start: datetime,
        observed_at: datetime,
    ) -> tuple[bytes, bool, bool]: ...

    def load(self, gap_key: str) -> bytes: ...

    def resolve(self, gap_key: str, resolution: CapabilityGapResolution) -> bytes: ...

    def set_export_state(self, gap_key: str, state: GapExportState) -> bytes: ...

    def set_export_states(
        self, gap_keys: Collection[str], state: GapExportState
    ) -> tuple[bytes, ...]: ...

    def list_aggregates(
        self, *, states: Collection[GapExportState] | None = None
    ) -> tuple[bytes, ...]: ...

    def claim_export_batch(self) -> tuple[bytes, ...]: ...

    def finalize_export_batch(
        self, expected: Mapping[str, bytes], state: GapExportState
    ) -> tuple[str, ...]: ...


class GapOutboxPublisher(Protocol):
    """Trusted local publication effect for an explicit outbox export."""

    def publish(self, payload: bytes) -> None: ...


class FeatureGapSink(Protocol):
    """Trusted inward sink used by the separately discoverable host tool."""

    def record(
        self,
        observation: CapabilityGapObservation,
        write_context: CapabilityGapWriteContext,
    ) -> CapabilityGapCompactView: ...


class CapabilityGapReportDispatcher(Protocol):
    """Host-injected bounded inward dispatch for learner-thread reporting.

    ``try_submit`` must be bounded and non-blocking.  The core does not own a
    worker, queue, or thread pool; the host chooses those operational details.
    """

    def try_submit(
        self, proposal: CapabilityGapProposal, context: CapabilityGapHostContext
    ) -> bool: ...


__all__ = [
    "CapabilityGapReportDispatcher",
    "CapabilityGapStore",
    "FeatureGapSink",
    "GapOutboxPublisher",
]
