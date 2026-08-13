"""Strict, local-only capability-gap outbox contracts and export service.

The outbox is deliberately narrower than the local aggregate.  It contains
only closed dimensions, trusted fingerprints, bounded counters, timestamps,
and lifecycle enums.  Publication is an injected local effect: this module
does not know how to reach a network, Flywheel, GitHub, or a provider.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol, cast

from study_agent.state import canonical_json_bytes, canonical_json_object

from .contracts import (
    CapabilityGapAggregate,
    CapabilityGapCollisionError,
    CapabilityGapCorruptionError,
    CapabilityGapDimensions,
    CapabilityGapValidationError,
    GapCategory,
    GapExportState,
    GapKeyV1,
    GapResolutionKind,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    TrustedLimitationCode,
    VerificationKind,
)

OUTBOX_SCHEMA_VERSION = 2
_OUTBOX_DOMAIN = b"study-agent-gap-outbox-v2\0"
_OUTBOX_KEY_DOMAIN = b"study-agent-gap-outbox-key-binding-v1\0"
_OUTBOX_HARNESS_DOMAIN = b"study-agent-gap-outbox-harness-v1\0"
_OUTBOX_IDENTITY_DOMAIN = b"study-agent-gap-contract-identity-v1\0"
_MAX_OUTBOX_BYTES = 512 * 1024
_VERSION = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_HEX64 = re.compile(r"^[0-9a-f]{64}$")


class GapOutboxValidationError(ValueError):
    """A caller supplied a value outside the strict outbox contract."""


class GapOutboxCorruptionError(RuntimeError):
    """Persisted or imported outbox bytes are not the exact contract."""


class GapOutboxPublicationError(RuntimeError):
    """The explicit local publisher did not durably publish the bundle."""


class GapOutboxPublisher(Protocol):
    """Trusted local publication effect injected by the embedding host.

    Implementations must return normally only after the exact bytes are
    durable.  A hosted/network implementation belongs to a later adapter and
    is intentionally not part of this core module.
    """

    def publish(self, payload: bytes) -> None: ...


def _digest(value: object, field: str) -> str:
    if not isinstance(value, str) or _HEX64.fullmatch(value) is None:
        raise GapOutboxValidationError(f"invalid_{field}")
    return value


def _fingerprint(value: str, domain: bytes) -> str:
    return sha256(domain + value.encode("utf-8")).hexdigest()


def _harness_fingerprint(value: str) -> str:
    _version(value)
    return _fingerprint(value, _OUTBOX_HARNESS_DOMAIN)


@dataclass(frozen=True, slots=True)
class GapOutboxDimensions:
    """Closed, redacted dimensions used by the portable outbox.

    The host-facing contract identity is deliberately reduced to a
    domain-separated digest before it crosses the publication boundary.
    """

    category: GapCategory
    requested_operation_kind: RequestedOperationKind
    safe_target_kind: SafeTargetKind
    limitation_code: TrustedLimitationCode
    contract_major: int
    contract_identity_fingerprint: str
    schema_version: int = 1

    @classmethod
    def from_dimensions(cls, dimensions: CapabilityGapDimensions) -> GapOutboxDimensions:
        if not isinstance(dimensions, CapabilityGapDimensions):
            raise GapOutboxValidationError("invalid_dimensions")
        return cls(
            category=dimensions.category,
            requested_operation_kind=dimensions.requested_operation_kind,
            safe_target_kind=dimensions.safe_target_kind,
            limitation_code=dimensions.limitation_code,
            contract_major=dimensions.contract_major,
            contract_identity_fingerprint=_fingerprint(
                dimensions.relevant_contract_identity, _OUTBOX_IDENTITY_DOMAIN
            ),
        )

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise GapOutboxValidationError("invalid_dimensions_schema_version")
        for value, enum_type, field_name in (
            (self.category, GapCategory, "category"),
            (self.requested_operation_kind, RequestedOperationKind, "requested_operation_kind"),
            (self.safe_target_kind, SafeTargetKind, "safe_target_kind"),
        ):
            if not isinstance(value, enum_type):
                raise GapOutboxValidationError(f"invalid_{field_name}")
        if not isinstance(self.limitation_code, TrustedLimitationCode):
            raise GapOutboxValidationError("invalid_limitation_code")
        if type(self.contract_major) is not int or self.contract_major < 1:
            raise GapOutboxValidationError("invalid_contract_major")
        _digest(self.contract_identity_fingerprint, "contract_identity_fingerprint")

    def to_json(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "contract_identity_fingerprint": self.contract_identity_fingerprint,
            "contract_major": self.contract_major,
            "limitation_code": self.limitation_code.value,
            "requested_operation_kind": self.requested_operation_kind.value,
            "safe_target_kind": self.safe_target_kind.value,
            "schema_version": self.schema_version,
        }

    @classmethod
    def from_json(cls, value: Mapping[str, object]) -> GapOutboxDimensions:
        fields = (
            "schema_version",
            "category",
            "requested_operation_kind",
            "safe_target_kind",
            "limitation_code",
            "contract_major",
            "contract_identity_fingerprint",
        )
        if tuple(sorted(value)) != tuple(sorted(fields)):
            raise GapOutboxCorruptionError("outbox_dimensions_invalid")
        try:
            return cls(
                schema_version=cast(int, value["schema_version"]),
                category=GapCategory(cast(str, value["category"])),
                requested_operation_kind=RequestedOperationKind(
                    cast(str, value["requested_operation_kind"])
                ),
                safe_target_kind=SafeTargetKind(cast(str, value["safe_target_kind"])),
                limitation_code=TrustedLimitationCode(cast(str, value["limitation_code"])),
                contract_major=cast(int, value["contract_major"]),
                contract_identity_fingerprint=cast(
                    str, value["contract_identity_fingerprint"]
                ),
            )
        except (KeyError, TypeError, ValueError, GapOutboxValidationError):
            raise GapOutboxCorruptionError("outbox_dimensions_invalid") from None

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))


def _version(value: object, field: str = "harness_version") -> str:
    if not isinstance(value, str) or _VERSION.fullmatch(value) is None:
        raise GapOutboxValidationError(f"invalid_{field}")
    return value


def _utc(value: object, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise GapOutboxValidationError(f"invalid_{field}")
    normalized = value.astimezone(UTC)
    return normalized


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise GapOutboxCorruptionError("outbox_record_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise GapOutboxCorruptionError("outbox_record_invalid") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise GapOutboxCorruptionError("outbox_record_invalid")
    return parsed.astimezone(UTC)


def _exact_object(data: bytes, fields: tuple[str, ...]) -> dict[str, Any]:
    if not isinstance(data, bytes) or len(data) > _MAX_OUTBOX_BYTES:
        raise GapOutboxCorruptionError("outbox_payload_invalid")
    try:
        value = canonical_json_object(data)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise GapOutboxCorruptionError("outbox_payload_invalid") from None
    if tuple(sorted(value)) != tuple(sorted(fields)):
        raise GapOutboxCorruptionError("outbox_payload_invalid")
    if canonical_json_bytes(cast(Any, value)) != data:
        raise GapOutboxCorruptionError("outbox_payload_noncanonical")
    return dict(value)


@dataclass(frozen=True, slots=True)
class GapOutboxRecord:
    """One immutable, credential-free projection of a local aggregate."""

    gap_key: GapKeyV1
    dimensions: GapOutboxDimensions
    verification_kind: VerificationKind
    impact_kind: ImpactKind
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    resolution: GapResolutionKind = GapResolutionKind.UNRESOLVED
    export_state: GapExportState = GapExportState.PENDING
    resolution_authority_fingerprint: str | None = None
    resolved_at: datetime | None = None
    schema_version: int = OUTBOX_SCHEMA_VERSION
    _source_dimensions: CapabilityGapDimensions | None = field(
        default=None, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != OUTBOX_SCHEMA_VERSION:
            raise GapOutboxValidationError("invalid_schema_version")
        if not isinstance(self.gap_key, GapKeyV1):
            raise GapOutboxValidationError("invalid_gap_key")
        if not isinstance(self.dimensions, GapOutboxDimensions):
            raise GapOutboxValidationError("invalid_dimensions")
        _digest(
            _fingerprint(
                self.gap_key.value + self.dimensions.to_bytes().decode("utf-8"),
                _OUTBOX_KEY_DOMAIN,
            ),
            "key_binding",
        )
        if not isinstance(self.verification_kind, VerificationKind):
            raise GapOutboxValidationError("invalid_verification_kind")
        if not isinstance(self.impact_kind, ImpactKind):
            raise GapOutboxValidationError("invalid_impact_kind")
        first = _utc(self.first_seen, "first_seen")
        last = _utc(self.last_seen, "last_seen")
        if last < first:
            raise GapOutboxValidationError("invalid_seen_range")
        if type(self.occurrence_count) is not int or self.occurrence_count < 1:
            raise GapOutboxValidationError("invalid_occurrence_count")
        if not isinstance(self.resolution, GapResolutionKind):
            raise GapOutboxValidationError("invalid_resolution")
        if not isinstance(self.export_state, GapExportState):
            raise GapOutboxValidationError("invalid_export_state")
        if self.resolution is GapResolutionKind.UNRESOLVED:
            if self.resolution_authority_fingerprint is not None or self.resolved_at is not None:
                raise GapOutboxValidationError("invalid_resolution")
        else:
            _digest(self.resolution_authority_fingerprint, "resolution_authority_fingerprint")
            if self.resolved_at is None:
                raise GapOutboxValidationError("invalid_resolution")
        if self.resolved_at is not None:
            resolved = _utc(self.resolved_at, "resolved_at")
            if resolved < first:
                raise GapOutboxValidationError("invalid_resolution")
            object.__setattr__(self, "resolved_at", resolved)
        object.__setattr__(self, "first_seen", first)
        object.__setattr__(self, "last_seen", last)

    @classmethod
    def from_aggregate(cls, aggregate: CapabilityGapAggregate) -> GapOutboxRecord:
        if not isinstance(aggregate, CapabilityGapAggregate):
            raise GapOutboxValidationError("invalid_aggregate")
        return cls(
            gap_key=aggregate.gap_key,
            dimensions=GapOutboxDimensions.from_dimensions(aggregate.dimensions),
            verification_kind=aggregate.verification_kind,
            impact_kind=aggregate.impact_kind,
            first_seen=aggregate.first_seen,
            last_seen=aggregate.last_seen,
            occurrence_count=aggregate.occurrence_count,
            resolution=aggregate.resolution,
            export_state=aggregate.export_state,
            resolution_authority_fingerprint=aggregate.resolution_authority_fingerprint,
            resolved_at=aggregate.resolved_at,
            _source_dimensions=aggregate.dimensions,
        )

    def to_aggregate(self) -> CapabilityGapAggregate:
        if self._source_dimensions is None:
            raise GapOutboxValidationError("redacted_record_not_aggregate")
        return CapabilityGapAggregate(
            gap_key=self.gap_key,
            dimensions=self._source_dimensions,
            verification_kind=self.verification_kind,
            impact_kind=self.impact_kind,
            first_seen=self.first_seen,
            last_seen=self.last_seen,
            occurrence_count=self.occurrence_count,
            resolution=self.resolution,
            export_state=self.export_state,
            resolution_authority_fingerprint=self.resolution_authority_fingerprint,
            resolved_at=self.resolved_at,
        )

    def to_json(self) -> dict[str, object]:
        return {
            "dimensions": self.dimensions.to_json(),
            "export_state": self.export_state.value,
            "first_seen": _timestamp(self.first_seen),
            "gap_key": self.gap_key.value,
            "impact_kind": self.impact_kind.value,
            "last_seen": _timestamp(self.last_seen),
            "occurrence_count": self.occurrence_count,
            "resolution": self.resolution.value,
            "resolution_authority_fingerprint": self.resolution_authority_fingerprint,
            "resolved_at": None if self.resolved_at is None else _timestamp(self.resolved_at),
            "schema_version": self.schema_version,
            "verification_kind": self.verification_kind.value,
            "key_binding": _fingerprint(
                self.gap_key.value + self.dimensions.to_bytes().decode("utf-8"),
                _OUTBOX_KEY_DOMAIN,
            ),
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> GapOutboxRecord:
        value = _exact_object(
            data,
            (
                "schema_version",
                "gap_key",
                "dimensions",
                "verification_kind",
                "impact_kind",
                "first_seen",
                "last_seen",
                "occurrence_count",
                "resolution",
                "export_state",
                "resolution_authority_fingerprint",
                "resolved_at",
                "key_binding",
            ),
        )
        try:
            nested = value["dimensions"]
            if not isinstance(nested, Mapping):
                raise ValueError
            dimensions = GapOutboxDimensions.from_json(nested)
            expected_binding = _fingerprint(
                str(value["gap_key"]) + dimensions.to_bytes().decode("utf-8"),
                _OUTBOX_KEY_DOMAIN,
            )
            if value["key_binding"] != expected_binding:
                raise CapabilityGapCollisionError("outbox_key_collision")
            record = cls(
                schema_version=value["schema_version"],
                gap_key=GapKeyV1(value["gap_key"]),
                dimensions=dimensions,
                verification_kind=VerificationKind(value["verification_kind"]),
                impact_kind=ImpactKind(value["impact_kind"]),
                first_seen=_parse_timestamp(value["first_seen"], "first_seen"),
                last_seen=_parse_timestamp(value["last_seen"], "last_seen"),
                occurrence_count=value["occurrence_count"],
                resolution=GapResolutionKind(value["resolution"]),
                export_state=GapExportState(value["export_state"]),
                resolution_authority_fingerprint=value["resolution_authority_fingerprint"],
                resolved_at=(
                    None
                    if value["resolved_at"] is None
                    else _parse_timestamp(value["resolved_at"], "resolved_at")
                ),
            )
        except CapabilityGapCollisionError:
            raise
        except (
            KeyError,
            TypeError,
            ValueError,
            CapabilityGapValidationError,
            CapabilityGapCorruptionError,
        ):
            raise GapOutboxCorruptionError("outbox_record_invalid") from None
        if record.to_bytes() != data:
            raise GapOutboxCorruptionError("outbox_payload_noncanonical")
        return record


@dataclass(frozen=True, slots=True)
class GapOutboxBundle:
    """Versioned deterministic bundle containing only sorted gap records."""

    harness_fingerprint: str
    records: tuple[GapOutboxRecord, ...]
    schema_version: int = OUTBOX_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != OUTBOX_SCHEMA_VERSION:
            raise GapOutboxValidationError("invalid_schema_version")
        _digest(self.harness_fingerprint, "harness_fingerprint")
        if not isinstance(self.records, tuple):
            raise GapOutboxValidationError("records_must_be_tuple")
        if any(not isinstance(item, GapOutboxRecord) for item in self.records):
            raise GapOutboxValidationError("invalid_record")
        keys = tuple(item.gap_key.value for item in self.records)
        if keys != tuple(sorted(keys)) or len(keys) != len(set(keys)):
            raise GapOutboxValidationError("records_not_canonical")

    @property
    def bundle_fingerprint(self) -> str:
        return sha256(_OUTBOX_DOMAIN + self.to_bytes()).hexdigest()

    def to_json(self) -> dict[str, object]:
        return {
            "harness_fingerprint": self.harness_fingerprint,
            "records": tuple(item.to_json() for item in self.records),
            "schema_version": self.schema_version,
        }

    def to_bytes(self) -> bytes:
        payload = canonical_json_bytes(cast(Any, self.to_json()))
        if len(payload) > _MAX_OUTBOX_BYTES:
            raise GapOutboxValidationError("outbox_too_large")
        return payload

    @classmethod
    def from_bytes(cls, data: bytes) -> GapOutboxBundle:
        value = _exact_object(data, ("schema_version", "harness_fingerprint", "records"))
        try:
            raw_records = value["records"]
            if not isinstance(raw_records, Sequence) or isinstance(raw_records, (str, bytes)):
                raise ValueError
            records = tuple(
                GapOutboxRecord.from_bytes(canonical_json_bytes(cast(Any, item)))
                for item in raw_records
            )
            bundle = cls(
                harness_fingerprint=value["harness_fingerprint"],
                records=records,
                schema_version=value["schema_version"],
            )
        except CapabilityGapCollisionError:
            raise
        except GapOutboxCorruptionError:
            raise
        except (KeyError, TypeError, ValueError, GapOutboxValidationError):
            raise GapOutboxCorruptionError("outbox_bundle_invalid") from None
        if bundle.to_bytes() != data:
            raise GapOutboxCorruptionError("outbox_payload_noncanonical")
        return bundle


@dataclass(frozen=True, slots=True)
class GapOutboxPublication:
    """Receipt returned only after the publisher accepted durable bytes."""

    bundle: GapOutboxBundle
    payload: bytes
    exported_gap_keys: tuple[GapKeyV1, ...]

    def __post_init__(self) -> None:
        if self.payload != self.bundle.to_bytes():
            raise GapOutboxValidationError("publication_payload_mismatch")
        if tuple(item.gap_key for item in self.bundle.records) != self.exported_gap_keys:
            raise GapOutboxValidationError("publication_keys_mismatch")

    @property
    def bundle_fingerprint(self) -> str:
        return self.bundle.bundle_fingerprint


class GapOutboxExportService:
    """Explicit local export coordinator with publish-before-export ordering."""

    def __init__(
        self,
        store: Any,
        publisher: GapOutboxPublisher,
        *,
        harness_version: str,
    ) -> None:
        _version(harness_version)
        if not callable(getattr(store, "claim_export_batch", None)):
            raise GapOutboxValidationError("store_claim_unsupported")
        if not callable(getattr(store, "finalize_export_batch", None)):
            raise GapOutboxValidationError("store_finalize_unsupported")
        if not callable(getattr(publisher, "publish", None)):
            raise GapOutboxValidationError("publisher_unsupported")
        self._store = store
        self._publisher = publisher
        self._harness_fingerprint = _harness_fingerprint(harness_version)

    def snapshot(self, *, include_exported: bool = False) -> GapOutboxBundle:
        states = (
            frozenset(GapExportState)
            if include_exported
            else frozenset(
                {GapExportState.LOCAL, GapExportState.PENDING, GapExportState.FAILED}
            )
        )
        payloads = self._store.list_aggregates(states=states)
        records = tuple(
            sorted(
                (
                    GapOutboxRecord.from_aggregate(_decode_aggregate(payload))
                    for payload in payloads
                ),
                key=lambda item: item.gap_key.value,
            )
        )
        return GapOutboxBundle(self._harness_fingerprint, records)

    def export_pending(self) -> GapOutboxPublication:
        """Publish a stable local snapshot and then mark its source rows.

        A failure leaves rows pending/failed; a crash after publication and
        before marking is safe because those rows remain pending and the next
        explicit call publishes the same bytes again.
        """

        payloads = self._store.claim_export_batch()
        claimed = sorted(
            [
                (GapOutboxRecord.from_aggregate(_decode_aggregate(payload)), payload)
                for payload in payloads
            ],
            key=lambda item: item[0].gap_key.value,
        )
        records = tuple(item[0] for item in claimed)
        bundle = GapOutboxBundle(self._harness_fingerprint, records)
        payload = bundle.to_bytes()
        expected = {record.gap_key.value: source for record, source in claimed}
        try:
            self._publisher.publish(payload)
        except Exception as error:
            with suppress(Exception):
                self._store.finalize_export_batch(expected, GapExportState.FAILED)
            raise GapOutboxPublicationError("outbox_publish_failed") from error
        finalized = self._store.finalize_export_batch(expected, GapExportState.EXPORTED)
        if len(finalized) != len(expected):
            raise GapOutboxPublicationError("outbox_snapshot_changed")
        return GapOutboxPublication(bundle, payload, tuple(item.gap_key for item in bundle.records))

    # Concise aliases make the explicit action convenient without introducing
    # a second semantic path.
    export = export_pending
    publish_pending = export_pending


def _decode_aggregate(payload: object) -> CapabilityGapAggregate:
    if not isinstance(payload, bytes):
        raise GapOutboxCorruptionError("outbox_source_invalid")
    try:
        return CapabilityGapAggregate.from_bytes(payload)
    except (CapabilityGapCorruptionError, CapabilityGapCollisionError):
        raise GapOutboxCorruptionError("outbox_source_invalid") from None


# Versioned spellings are useful to downstream adapters while the short names
# remain ergonomic for the core and tests.
GapOutboxRecordV1 = GapOutboxRecord
GapOutboxBundleV1 = GapOutboxBundle
GapOutboxExportReceipt = GapOutboxPublication


__all__ = [
    "OUTBOX_SCHEMA_VERSION",
    "GapOutboxBundle",
    "GapOutboxBundleV1",
    "GapOutboxCorruptionError",
    "GapOutboxDimensions",
    "GapOutboxExportReceipt",
    "GapOutboxExportService",
    "GapOutboxPublication",
    "GapOutboxPublicationError",
    "GapOutboxPublisher",
    "GapOutboxRecord",
    "GapOutboxRecordV1",
    "GapOutboxValidationError",
]
