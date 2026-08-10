"""Strict, provider-neutral contracts for the capability-gap plane.

This module deliberately contains only closed structured values.  A gap report
is an operational observation, not a learner event or an instruction to change
the harness.  The codecs below are intentionally boring: their stable bytes
are the boundary used by the local registry and by future redacted exports.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from hashlib import sha256
from typing import Any, cast

from study_agent.state import canonical_json_bytes, canonical_json_object

_HEX64 = re.compile(r"^[0-9a-f]{64}$")
_OPAQUE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}$")
_MAX_CODEC_BYTES = 64 * 1024
_UNVERIFIED_CONTRACT = "unverified-request@1"
_UNVERIFIED_MAJOR = 1
_KEY_DOMAIN = b"study-agent-gap-key-v1\0"
_REPORT_DOMAIN = b"study-agent-gap-report-v1\0"


class GapCategory(StrEnum):
    INPUT_FORMAT = "input_format"
    OUTPUT_FORMAT = "output_format"
    STUDY_BEHAVIOR = "study_behavior"
    INTEGRATION = "integration"
    ACCESSIBILITY = "accessibility"
    PERFORMANCE = "performance"
    RELIABILITY = "reliability"


class RequestedOperationKind(StrEnum):
    INGEST_SOURCE = "ingest_source"
    EXTRACT_TEXT = "extract_text"
    PRESERVE_TABLES = "preserve_tables"
    GENERATE_STUDY_ARTIFACT = "generate_study_artifact"
    ASSESS_LEARNER = "assess_learner"
    INTEGRATE_SERVICE = "integrate_service"
    RENDER_ACCESSIBLY = "render_accessibly"
    REDUCE_LATENCY = "reduce_latency"
    RECOVER_OPERATION = "recover_operation"


class SafeTargetKind(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"
    PDF = "pdf"
    IMAGE = "image"
    AUDIO = "audio"
    VIDEO = "video"
    TABULAR = "tabular"
    EXTERNAL_SERVICE = "external_service"
    STUDY_SESSION = "study_session"
    STUDY_ARTIFACT = "study_artifact"
    RUNTIME = "runtime"


class TrustedLimitationCode(StrEnum):
    UNSUPPORTED_FORMAT = "unsupported_format"
    MISSING_CAPABILITY = "missing_capability"
    MISSING_INTEGRATION = "missing_integration"
    INACCESSIBLE_CONTENT = "inaccessible_content"
    RESOURCE_LIMIT = "resource_limit"
    TRANSIENT_FAILURE = "transient_failure"
    RELIABILITY_FAILURE = "reliability_failure"


class ImpactKind(StrEnum):
    BLOCKED = "blocked"
    DEGRADED = "degraded"
    WORKAROUND_AVAILABLE = "workaround_available"


class VerificationKind(StrEnum):
    UNVERIFIED_REQUEST = "unverified_request"
    VERIFIED_RUNTIME_FAILURE = "verified_runtime_failure"


class GapDisposition(StrEnum):
    RECORDED = "recorded"
    DEDUPLICATED = "deduplicated"
    RATE_LIMITED = "rate_limited"


class GapResolutionKind(StrEnum):
    """Trusted maintainer lifecycle state for an operational observation."""

    UNRESOLVED = "unresolved"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    DEFERRED = "deferred"
    ACCEPTED = "accepted"


class GapExportState(StrEnum):
    """Local export lifecycle; export never implies delivery or acceptance."""

    LOCAL = "local"
    PENDING = "pending"
    EXPORTED = "exported"
    FAILED = "failed"


class CapabilityGapValidationError(ValueError):
    """The caller supplied an invalid or non-canonical gap value."""


class CapabilityGapCollisionError(RuntimeError):
    """A key, aggregate, or report identity was used inconsistently."""


class CapabilityGapCorruptionError(RuntimeError):
    """Persisted registry bytes or schema are not the contract."""


class CapabilityGapUnavailableError(RuntimeError):
    """The requested operational record does not exist or is unavailable."""


@dataclass(frozen=True, slots=True)
class CapabilityGapResolution:
    """A maintainer-authored resolution, kept separate from learner evidence."""

    kind: GapResolutionKind
    authority_fingerprint: str
    resolved_at: datetime

    def __post_init__(self) -> None:
        _enum(self.kind, GapResolutionKind, field="resolution")
        _digest(self.authority_fingerprint, field="authority_fingerprint")
        _utc(self.resolved_at, field="resolved_at")
        if self.kind is GapResolutionKind.UNRESOLVED:
            raise CapabilityGapValidationError("invalid_resolution")

    def to_json(self) -> dict[str, object]:
        return {
            "authority_fingerprint": self.authority_fingerprint,
            "kind": self.kind.value,
            "resolved_at": _timestamp(self.resolved_at),
            "schema_version": 1,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> CapabilityGapResolution:
        value = _exact_object(
            data, ("schema_version", "kind", "authority_fingerprint", "resolved_at")
        )
        try:
            result = cls(
                GapResolutionKind(value["kind"]),
                value["authority_fingerprint"],
                _parse_timestamp(value["resolved_at"], field="resolved_at"),
            )
        except (KeyError, TypeError, ValueError, CapabilityGapValidationError):
            raise CapabilityGapCorruptionError("gap_payload_invalid") from None
        if result.to_bytes() != data:
            raise CapabilityGapCorruptionError("gap_payload_noncanonical")
        return result


def _error(error_type: type[Exception], code: str) -> Exception:
    return error_type(code)


def _text(value: object, *, field: str, maximum: int = 128) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise CapabilityGapValidationError(f"invalid_{field}")
    if len(value) > maximum or any(ord(char) < 32 for char in value):
        raise CapabilityGapValidationError(f"invalid_{field}")
    return value


def _opaque(value: object, *, field: str) -> str:
    text = _text(value, field=field)
    if _OPAQUE.fullmatch(text) is None:
        raise CapabilityGapValidationError(f"invalid_{field}")
    return text


def _digest(value: object, *, field: str) -> str:
    text = _text(value, field=field, maximum=64)
    if _HEX64.fullmatch(text) is None:
        raise CapabilityGapValidationError(f"invalid_{field}")
    return text


def _enum(value: object, enum_type: type[StrEnum], *, field: str) -> StrEnum:
    if not isinstance(value, enum_type):
        raise CapabilityGapValidationError(f"invalid_{field}")
    return value


def _positive_int(value: object, *, field: str) -> int:
    if type(value) is not int or value < 1:
        raise CapabilityGapValidationError(f"invalid_{field}")
    return value


def _utc(value: object, *, field: str) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise CapabilityGapValidationError(f"invalid_{field}")
    return value.astimezone(UTC)


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _parse_timestamp(value: object, *, field: str) -> datetime:
    if not isinstance(value, str):
        raise CapabilityGapCorruptionError("gap_payload_invalid")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise CapabilityGapCorruptionError("gap_payload_invalid") from None
    return _utc_or_corruption(parsed, field=field)


def _utc_or_corruption(value: datetime, *, field: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise CapabilityGapCorruptionError("gap_payload_invalid")
    return value.astimezone(UTC)


def _exact_object(data: bytes, fields: tuple[str, ...]) -> Mapping[str, Any]:
    if not isinstance(data, bytes) or len(data) > _MAX_CODEC_BYTES:
        raise CapabilityGapCorruptionError("gap_payload_invalid")
    try:
        value = canonical_json_object(data)
    except (TypeError, ValueError, UnicodeDecodeError):
        raise CapabilityGapCorruptionError("gap_payload_invalid") from None
    if tuple(sorted(value.keys())) != tuple(sorted(fields)):
        raise CapabilityGapCorruptionError("gap_payload_invalid")
    if canonical_json_bytes(cast(Mapping[str, Any], value)) != data:
        raise CapabilityGapCorruptionError("gap_payload_noncanonical")
    return cast(Mapping[str, Any], value)


@dataclass(frozen=True, slots=True)
class TrustedLimitationReceipt:
    contract_identity: str
    contract_major: int
    limitation_code: TrustedLimitationCode
    failure_fingerprint: str

    def __post_init__(self) -> None:
        _opaque(self.contract_identity, field="contract_identity")
        _positive_int(self.contract_major, field="contract_major")
        _enum(self.limitation_code, TrustedLimitationCode, field="limitation_code")
        _digest(self.failure_fingerprint, field="failure_fingerprint")

    def to_json(self) -> dict[str, object]:
        return {
            "contract_identity": self.contract_identity,
            "contract_major": self.contract_major,
            "failure_fingerprint": self.failure_fingerprint,
            "limitation_code": self.limitation_code.value,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> TrustedLimitationReceipt:
        value = _exact_object(
            data,
            ("contract_identity", "contract_major", "limitation_code", "failure_fingerprint"),
        )
        try:
            receipt = cls(
                contract_identity=value["contract_identity"],
                contract_major=value["contract_major"],
                limitation_code=TrustedLimitationCode(value["limitation_code"]),
                failure_fingerprint=value["failure_fingerprint"],
            )
        except (KeyError, TypeError, ValueError):
            raise CapabilityGapCorruptionError("gap_payload_invalid") from None
        if receipt.to_bytes() != data:
            raise CapabilityGapCorruptionError("gap_payload_noncanonical")
        return receipt


# The public name used by the feedback specification.  Keep the historical
# ``TrustedLimitationReceipt`` spelling as a compatibility alias: both names
# describe host-trusted typed failure evidence and neither accepts model text.
CapabilityGapFailureEvidence = TrustedLimitationReceipt


@dataclass(frozen=True, slots=True)
class CapabilityGapDimensions:
    schema_version: int
    category: GapCategory
    requested_operation_kind: RequestedOperationKind
    safe_target_kind: SafeTargetKind
    limitation_code: TrustedLimitationCode
    relevant_contract_identity: str
    contract_major: int

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version != 1:
            raise CapabilityGapValidationError("invalid_schema_version")
        _enum(self.category, GapCategory, field="category")
        _enum(
            self.requested_operation_kind,
            RequestedOperationKind,
            field="requested_operation_kind",
        )
        _enum(self.safe_target_kind, SafeTargetKind, field="safe_target_kind")
        _enum(self.limitation_code, TrustedLimitationCode, field="limitation_code")
        _opaque(self.relevant_contract_identity, field="relevant_contract_identity")
        _positive_int(self.contract_major, field="contract_major")

    def to_json(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "contract_major": self.contract_major,
            "limitation_code": self.limitation_code.value,
            "relevant_contract_identity": self.relevant_contract_identity,
            "requested_operation_kind": self.requested_operation_kind.value,
            "safe_target_kind": self.safe_target_kind.value,
            "schema_version": self.schema_version,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> CapabilityGapDimensions:
        value = _exact_object(
            data,
            (
                "schema_version",
                "category",
                "requested_operation_kind",
                "safe_target_kind",
                "limitation_code",
                "relevant_contract_identity",
                "contract_major",
            ),
        )
        try:
            return cls(
                schema_version=value["schema_version"],
                category=GapCategory(value["category"]),
                requested_operation_kind=RequestedOperationKind(value["requested_operation_kind"]),
                safe_target_kind=SafeTargetKind(value["safe_target_kind"]),
                limitation_code=TrustedLimitationCode(value["limitation_code"]),
                relevant_contract_identity=value["relevant_contract_identity"],
                contract_major=value["contract_major"],
            )
        except (KeyError, TypeError, ValueError):
            raise CapabilityGapCorruptionError("gap_payload_invalid") from None


@dataclass(frozen=True, slots=True)
class GapKeyV1:
    value: str

    def __post_init__(self) -> None:
        _digest(self.value, field="gap_key")

    def __str__(self) -> str:
        return self.value

    @classmethod
    def derive(cls, dimensions: CapabilityGapDimensions) -> GapKeyV1:
        if not isinstance(dimensions, CapabilityGapDimensions):
            raise CapabilityGapValidationError("invalid_dimensions")
        return cls(sha256(_KEY_DOMAIN + dimensions.to_bytes()).hexdigest())

    @classmethod
    def from_dimensions(cls, dimensions: CapabilityGapDimensions) -> GapKeyV1:
        return cls.derive(dimensions)

    def verify(self, dimensions: CapabilityGapDimensions) -> None:
        if self != self.derive(dimensions):
            raise CapabilityGapCollisionError("gap_key_collision")


@dataclass(frozen=True, slots=True)
class CapabilityGapObservation:
    category: GapCategory
    requested_operation_kind: RequestedOperationKind
    safe_target_kind: SafeTargetKind
    impact_kind: ImpactKind

    def __post_init__(self) -> None:
        _enum(self.category, GapCategory, field="category")
        _enum(
            self.requested_operation_kind,
            RequestedOperationKind,
            field="requested_operation_kind",
        )
        _enum(self.safe_target_kind, SafeTargetKind, field="safe_target_kind")
        _enum(self.impact_kind, ImpactKind, field="impact_kind")

    def to_json(self) -> dict[str, object]:
        return {
            "category": self.category.value,
            "impact_kind": self.impact_kind.value,
            "requested_operation_kind": self.requested_operation_kind.value,
            "safe_target_kind": self.safe_target_kind.value,
            "schema_version": 1,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> CapabilityGapObservation:
        value = _exact_object(
            data,
            (
                "schema_version",
                "category",
                "requested_operation_kind",
                "safe_target_kind",
                "impact_kind",
            ),
        )
        try:
            if value["schema_version"] != 1:
                raise ValueError
            return cls(
                category=GapCategory(value["category"]),
                requested_operation_kind=RequestedOperationKind(value["requested_operation_kind"]),
                safe_target_kind=SafeTargetKind(value["safe_target_kind"]),
                impact_kind=ImpactKind(value["impact_kind"]),
            )
        except (KeyError, TypeError, ValueError):
            raise CapabilityGapCorruptionError("gap_payload_invalid") from None


@dataclass(frozen=True, slots=True)
class CapabilityGapWriteContext:
    harness_version: str
    correlation_id: str
    idempotency_fingerprint: str
    observed_at: datetime
    limitation_receipt: TrustedLimitationReceipt | None = None

    def __post_init__(self) -> None:
        _opaque(self.harness_version, field="harness_version")
        _opaque(self.correlation_id, field="correlation_id")
        _digest(self.idempotency_fingerprint, field="idempotency_fingerprint")
        _utc(self.observed_at, field="observed_at")
        if self.limitation_receipt is not None and not isinstance(
            self.limitation_receipt, TrustedLimitationReceipt
        ):
            raise CapabilityGapValidationError("invalid_limitation_receipt")


@dataclass(frozen=True, slots=True)
class CapabilityGapAggregate:
    gap_key: GapKeyV1
    dimensions: CapabilityGapDimensions
    verification_kind: VerificationKind
    impact_kind: ImpactKind
    first_seen: datetime
    last_seen: datetime
    occurrence_count: int
    resolution: GapResolutionKind = GapResolutionKind.UNRESOLVED
    export_state: GapExportState = GapExportState.LOCAL
    resolution_authority_fingerprint: str | None = None
    resolved_at: datetime | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.gap_key, GapKeyV1):
            raise CapabilityGapValidationError("invalid_gap_key")
        if not isinstance(self.dimensions, CapabilityGapDimensions):
            raise CapabilityGapValidationError("invalid_dimensions")
        self.gap_key.verify(self.dimensions)
        _enum(self.verification_kind, VerificationKind, field="verification_kind")
        _enum(self.impact_kind, ImpactKind, field="impact_kind")
        first = _utc(self.first_seen, field="first_seen")
        last = _utc(self.last_seen, field="last_seen")
        if last < first:
            raise CapabilityGapValidationError("invalid_seen_range")
        _positive_int(self.occurrence_count, field="occurrence_count")
        _enum(self.resolution, GapResolutionKind, field="resolution")
        _enum(self.export_state, GapExportState, field="export_state")
        if self.resolution is GapResolutionKind.UNRESOLVED:
            if self.resolution_authority_fingerprint is not None or self.resolved_at is not None:
                raise CapabilityGapValidationError("invalid_resolution")
        else:
            _digest(
                self.resolution_authority_fingerprint,
                field="resolution_authority_fingerprint",
            )
            if self.resolved_at is None:
                raise CapabilityGapValidationError("invalid_resolution")
            resolved = _utc(self.resolved_at, field="resolved_at")
            if resolved != self.resolved_at:
                object.__setattr__(self, "resolved_at", resolved)
        if self.resolved_at is not None and self.resolved_at < first:
            raise CapabilityGapValidationError("invalid_resolution")
        if first != self.first_seen or last != self.last_seen:
            object.__setattr__(self, "first_seen", first)
            object.__setattr__(self, "last_seen", last)

    def to_json(self) -> dict[str, object]:
        return {
            "dimensions": self.dimensions.to_json(),
            "first_seen": _timestamp(self.first_seen),
            "gap_key": self.gap_key.value,
            "impact_kind": self.impact_kind.value,
            "last_seen": _timestamp(self.last_seen),
            "occurrence_count": self.occurrence_count,
            "export_state": self.export_state.value,
            "resolution": self.resolution.value,
            "resolution_authority_fingerprint": self.resolution_authority_fingerprint,
            "resolved_at": None if self.resolved_at is None else _timestamp(self.resolved_at),
            "schema_version": 1,
            "verification_kind": self.verification_kind.value,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(cast(Any, self.to_json()))

    @classmethod
    def from_bytes(cls, data: bytes) -> CapabilityGapAggregate:
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
            ),
        )
        dimensions_value = value.get("dimensions")
        try:
            if value["schema_version"] != 1 or not isinstance(dimensions_value, Mapping):
                raise ValueError
            dimensions = CapabilityGapDimensions.from_bytes(
                canonical_json_bytes(cast(Any, dimensions_value))
            )
            aggregate = cls(
                gap_key=GapKeyV1(value["gap_key"]),
                dimensions=dimensions,
                verification_kind=VerificationKind(value["verification_kind"]),
                impact_kind=ImpactKind(value["impact_kind"]),
                first_seen=_parse_timestamp(value["first_seen"], field="first_seen"),
                last_seen=_parse_timestamp(value["last_seen"], field="last_seen"),
                occurrence_count=value["occurrence_count"],
                resolution=GapResolutionKind(value["resolution"]),
                export_state=GapExportState(value["export_state"]),
                resolution_authority_fingerprint=value["resolution_authority_fingerprint"],
                resolved_at=(
                    None
                    if value["resolved_at"] is None
                    else _parse_timestamp(value["resolved_at"], field="resolved_at")
                ),
            )
        except CapabilityGapCollisionError:
            raise
        except CapabilityGapCorruptionError:
            raise
        except (KeyError, TypeError, ValueError):
            raise CapabilityGapCorruptionError("gap_payload_invalid") from None
        # Canonical aggregate bytes must round-trip exactly.  This also catches
        # timestamp spellings that happen to parse but are not the contract
        # representation.
        if aggregate.to_bytes() != data:
            raise CapabilityGapCorruptionError("gap_payload_noncanonical")
        return aggregate


def report_id_for(gap_key: GapKeyV1, idempotency_fingerprint: str) -> str:
    if not isinstance(gap_key, GapKeyV1):
        raise CapabilityGapValidationError("invalid_gap_key")
    fingerprint = _digest(idempotency_fingerprint, field="idempotency_fingerprint")
    return sha256(
        _REPORT_DOMAIN + gap_key.value.encode() + b"\0" + fingerprint.encode()
    ).hexdigest()


def proposal_for(
    observation: CapabilityGapObservation,
    context: CapabilityGapWriteContext,
) -> CapabilityGapAggregate:
    if not isinstance(observation, CapabilityGapObservation) or not isinstance(
        context, CapabilityGapWriteContext
    ):
        raise CapabilityGapValidationError("invalid_gap_context")
    receipt = context.limitation_receipt
    if receipt is None:
        limitation = TrustedLimitationCode.MISSING_CAPABILITY
        verification = VerificationKind.UNVERIFIED_REQUEST
        identity = _UNVERIFIED_CONTRACT
        major = _UNVERIFIED_MAJOR
    else:
        limitation = receipt.limitation_code
        verification = VerificationKind.VERIFIED_RUNTIME_FAILURE
        identity = receipt.contract_identity
        major = receipt.contract_major
    dimensions = CapabilityGapDimensions(
        schema_version=1,
        category=observation.category,
        requested_operation_kind=observation.requested_operation_kind,
        safe_target_kind=observation.safe_target_kind,
        limitation_code=limitation,
        relevant_contract_identity=identity,
        contract_major=major,
    )
    key = GapKeyV1.derive(dimensions)
    seen = _utc(context.observed_at, field="observed_at")
    return CapabilityGapAggregate(
        gap_key=key,
        dimensions=dimensions,
        verification_kind=verification,
        impact_kind=observation.impact_kind,
        first_seen=seen,
        last_seen=seen,
        occurrence_count=1,
    )


__all__ = [
    "CapabilityGapAggregate",
    "CapabilityGapCollisionError",
    "CapabilityGapCorruptionError",
    "CapabilityGapDimensions",
    "CapabilityGapFailureEvidence",
    "CapabilityGapObservation",
    "CapabilityGapResolution",
    "CapabilityGapUnavailableError",
    "CapabilityGapValidationError",
    "CapabilityGapWriteContext",
    "GapCategory",
    "GapDisposition",
    "GapExportState",
    "GapKeyV1",
    "GapResolutionKind",
    "ImpactKind",
    "RequestedOperationKind",
    "SafeTargetKind",
    "TrustedLimitationCode",
    "TrustedLimitationReceipt",
    "VerificationKind",
    "proposal_for",
    "report_id_for",
]
