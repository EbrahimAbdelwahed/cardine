"""Application service for recording and querying sanitized capability gaps."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from study_agent.ports.capability_gap import CapabilityGapStore

from .contracts import (
    CapabilityGapAggregate,
    CapabilityGapObservation,
    CapabilityGapResolution,
    CapabilityGapUnavailableError,
    CapabilityGapValidationError,
    CapabilityGapWriteContext,
    GapDisposition,
    GapExportState,
    GapKeyV1,
    GapResolutionKind,
    proposal_for,
    report_id_for,
)
from .view import CapabilityGapCompactView, CapabilityGapDetailView


@dataclass(frozen=True, slots=True)
class GapRatePolicy:
    """Conservative per-key rate bound applied before a write."""

    max_occurrences: int = 1000
    window: timedelta = timedelta(minutes=15)

    def __post_init__(self) -> None:
        if type(self.max_occurrences) is not int or self.max_occurrences < 1:
            raise CapabilityGapValidationError("invalid_rate_policy")
        if self.window <= timedelta(0):
            raise CapabilityGapValidationError("invalid_rate_policy")


@dataclass(frozen=True, slots=True)
class GapRetentionPolicy:
    """Bounded local retention; expiry is explicit and never exported as evidence."""

    max_age: timedelta = timedelta(days=30)

    def __post_init__(self) -> None:
        if self.max_age <= timedelta(0):
            raise CapabilityGapValidationError("invalid_retention_policy")


class CapabilityGapService:
    """Record one closed observation in a host-owned operational store."""

    def __init__(
        self,
        store: CapabilityGapStore,
        *,
        rate_policy: GapRatePolicy | None = None,
        retention_policy: GapRetentionPolicy | None = None,
    ) -> None:
        self._store = store
        self._rate_policy = rate_policy or GapRatePolicy()
        self._retention_policy = retention_policy or GapRetentionPolicy()

    def record(
        self,
        observation: CapabilityGapObservation,
        context: CapabilityGapWriteContext,
    ) -> CapabilityGapCompactView:
        if not isinstance(observation, CapabilityGapObservation) or not isinstance(
            context, CapabilityGapWriteContext
        ):
            raise CapabilityGapValidationError("invalid_gap_context")
        proposed = proposal_for(observation, context)
        report_id = report_id_for(proposed.gap_key, context.idempotency_fingerprint)
        # Retention is host policy and intentionally best-effort for custom
        # stores; the reference SQLite adapter implements it transactionally.
        prune = getattr(self._store, "prune", None)
        if callable(prune):
            prune(context.observed_at - self._retention_policy.max_age)
        rate_limited = False
        atomic_record = getattr(self._store, "create_or_increment_rate_limited", None)
        if callable(atomic_record):
            payload, created, rate_limited = atomic_record(
                proposed.gap_key.value,
                report_id,
                proposed.to_bytes(),
                max_occurrences=self._rate_policy.max_occurrences,
                window_start=(
                    context.observed_at.astimezone(UTC) - self._rate_policy.window
                ),
                observed_at=context.observed_at,
            )
        else:
            # Custom stores predating the rate-aware inward port retain the
            # conservative local guard.  The reference SQLite adapter uses
            # the atomic path above, including exact-retry precedence.
            try:
                existing = self.get(proposed.gap_key)
            except CapabilityGapUnavailableError:
                existing = None
            if (
                existing is not None
                and existing.last_seen
                >= (context.observed_at.astimezone(UTC) - self._rate_policy.window)
                and existing.occurrence_count >= self._rate_policy.max_occurrences
            ):
                return CapabilityGapCompactView(
                    report_id=report_id,
                    gap_key=existing.gap_key,
                    occurrence_count=existing.occurrence_count,
                    disposition=GapDisposition.RATE_LIMITED,
                )
            payload, created = self._store.create_or_increment(
                proposed.gap_key.value, report_id, proposed.to_bytes()
            )
        try:
            aggregate = CapabilityGapAggregate.from_bytes(payload)
        except Exception as error:
            # Do not leak storage/JSON implementation details across the API.
            from .contracts import CapabilityGapCorruptionError

            if isinstance(error, CapabilityGapCorruptionError):
                raise
            raise CapabilityGapCorruptionError("gap_payload_invalid") from None
        if (
            aggregate.gap_key != proposed.gap_key
            or aggregate.dimensions != proposed.dimensions
            or aggregate.verification_kind != proposed.verification_kind
            or aggregate.impact_kind != proposed.impact_kind
        ):
            from .contracts import CapabilityGapCollisionError

            raise CapabilityGapCollisionError("gap_key_collision")
        return CapabilityGapCompactView(
            report_id=report_id,
            gap_key=aggregate.gap_key,
            occurrence_count=aggregate.occurrence_count,
            disposition=(
                GapDisposition.RATE_LIMITED
                if rate_limited
                else (GapDisposition.RECORDED if created else GapDisposition.DEDUPLICATED)
            ),
        )

    def resolve(
        self,
        gap_key: GapKeyV1 | str,
        kind: GapResolutionKind,
        authority_fingerprint: str,
        resolved_at: datetime,
    ) -> CapabilityGapDetailView:
        key = gap_key if isinstance(gap_key, GapKeyV1) else GapKeyV1(gap_key)
        resolution = CapabilityGapResolution(kind, authority_fingerprint, resolved_at)
        payload = self._store.resolve(key.value, resolution)
        return CapabilityGapDetailView.from_aggregate(CapabilityGapAggregate.from_bytes(payload))

    def set_export_state(
        self, gap_key: GapKeyV1 | str, state: GapExportState
    ) -> CapabilityGapDetailView:
        key = gap_key if isinstance(gap_key, GapKeyV1) else GapKeyV1(gap_key)
        payload = self._store.set_export_state(key.value, state)
        return CapabilityGapDetailView.from_aggregate(CapabilityGapAggregate.from_bytes(payload))

    def prune(self, before: datetime) -> int:
        prune = getattr(self._store, "prune", None)
        if not callable(prune):
            return 0
        return int(prune(before))

    def get(self, gap_key: GapKeyV1 | str) -> CapabilityGapDetailView:
        key = gap_key if isinstance(gap_key, GapKeyV1) else GapKeyV1(gap_key)
        payload = self._store.load(key.value)
        aggregate = CapabilityGapAggregate.from_bytes(payload)
        if aggregate.gap_key != key:
            from .contracts import CapabilityGapCollisionError

            raise CapabilityGapCollisionError("gap_key_collision")
        return CapabilityGapDetailView.from_aggregate(aggregate)


__all__ = ["CapabilityGapService", "GapRatePolicy", "GapRetentionPolicy"]
