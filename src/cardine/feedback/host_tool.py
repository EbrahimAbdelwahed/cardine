"""Minimal agent-facing capability-gap reporting tool.

The proposal is model-authored but contains only a closed vocabulary.  All
identity, authority, runtime evidence, and persistence are supplied by the
embedding host through :class:`CapabilityGapHostContext`.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from study_agent.domain._validation import JsonObject, freeze_object
from study_agent.feedback.contracts import (
    CapabilityGapObservation,
    CapabilityGapValidationError,
    CapabilityGapWriteContext,
    GapCategory,
    GapDisposition,
    GapKeyV1,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    TrustedLimitationCode,
    TrustedLimitationReceipt,
)
from study_agent.feedback.view import CapabilityGapCompactView
from study_agent.ports.capability_gap import (
    CapabilityGapReportDispatcher,
    FeatureGapSink,
)
from study_agent.state import canonical_json_bytes, canonical_json_object
from study_agent.tools.schema import validate_schema_definition


class WorkaroundSuggestionKind(StrEnum):
    NONE = "none"
    RETRY_LATER = "retry_later"
    USE_SUPPORTED_FORMAT = "use_supported_format"
    MANUAL_ENTRY = "manual_entry"
    USE_EXISTING_CAPABILITY = "use_existing_capability"


class CapabilityGapHostToolError(RuntimeError):
    """A bounded host-facing failure; storage/provider details never escape."""


@dataclass(frozen=True, slots=True)
class CapabilityGapCapabilityComparison:
    """Typed host proof that a closed operation is not available."""

    requested_operation_kind: RequestedOperationKind
    safe_target_kind: SafeTargetKind
    supported: bool
    contract_identity: str
    contract_major: int
    comparison_fingerprint: str

    def __post_init__(self) -> None:
        if not isinstance(self.requested_operation_kind, RequestedOperationKind):
            raise CapabilityGapValidationError("invalid_requested_operation_kind")
        if not isinstance(self.safe_target_kind, SafeTargetKind):
            raise CapabilityGapValidationError("invalid_safe_target_kind")
        if type(self.supported) is not bool or self.supported:
            raise CapabilityGapValidationError("capability_available")
        # TrustedLimitationReceipt performs the opaque identity/digest checks.
        TrustedLimitationReceipt(
            self.contract_identity,
            self.contract_major,
            TrustedLimitationCode.MISSING_CAPABILITY,
            self.comparison_fingerprint,
        )

    def limitation_receipt(self) -> TrustedLimitationReceipt:
        return TrustedLimitationReceipt(
            self.contract_identity,
            self.contract_major,
            TrustedLimitationCode.MISSING_CAPABILITY,
            self.comparison_fingerprint,
        )


@dataclass(frozen=True, slots=True)
class CapabilityGapProposal:
    category: GapCategory
    requested_operation_kind: RequestedOperationKind
    safe_target_kind: SafeTargetKind
    impact_kind: ImpactKind
    workaround_suggestion_kind: WorkaroundSuggestionKind = WorkaroundSuggestionKind.NONE

    def __post_init__(self) -> None:
        if not isinstance(self.category, GapCategory):
            raise CapabilityGapValidationError("invalid_category")
        if not isinstance(self.requested_operation_kind, RequestedOperationKind):
            raise CapabilityGapValidationError("invalid_requested_operation_kind")
        if not isinstance(self.safe_target_kind, SafeTargetKind):
            raise CapabilityGapValidationError("invalid_safe_target_kind")
        if not isinstance(self.impact_kind, ImpactKind):
            raise CapabilityGapValidationError("invalid_impact_kind")
        if not isinstance(self.workaround_suggestion_kind, WorkaroundSuggestionKind):
            raise CapabilityGapValidationError("invalid_workaround_suggestion_kind")

    def to_json(self) -> JsonObject:
        return {
            "category": self.category.value,
            "impact_kind": self.impact_kind.value,
            "requested_operation_kind": self.requested_operation_kind.value,
            "safe_target_kind": self.safe_target_kind.value,
            "workaround_suggestion_kind": self.workaround_suggestion_kind.value,
        }

    def to_bytes(self) -> bytes:
        return canonical_json_bytes(self.to_json())

    @classmethod
    def from_bytes(cls, data: bytes) -> CapabilityGapProposal:
        if not isinstance(data, bytes) or len(data) > 16 * 1024:
            raise CapabilityGapValidationError("invalid_proposal")
        try:
            value = canonical_json_object(data)
        except (TypeError, ValueError, UnicodeDecodeError):
            raise CapabilityGapValidationError("invalid_proposal") from None
        fields = set(value)
        required = {
            "category",
            "requested_operation_kind",
            "safe_target_kind",
            "impact_kind",
        }
        if fields not in (required, required | {"workaround_suggestion_kind"}):
            raise CapabilityGapValidationError("invalid_proposal_fields")
        if canonical_json_bytes(value) != data:
            raise CapabilityGapValidationError("noncanonical_proposal")
        try:
            return cls(
                category=GapCategory(_string(value["category"], "category")),
                requested_operation_kind=RequestedOperationKind(
                    _string(value["requested_operation_kind"], "requested_operation_kind")
                ),
                safe_target_kind=SafeTargetKind(
                    _string(value["safe_target_kind"], "safe_target_kind")
                ),
                impact_kind=ImpactKind(_string(value["impact_kind"], "impact_kind")),
                workaround_suggestion_kind=WorkaroundSuggestionKind(
                    _string(
                        value.get("workaround_suggestion_kind", WorkaroundSuggestionKind.NONE),
                        "workaround_suggestion_kind",
                    )
                ),
            )
        except (KeyError, TypeError, ValueError):
            raise CapabilityGapValidationError("invalid_proposal") from None


def _string(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise CapabilityGapValidationError(f"invalid_{field}")
    return value


@dataclass(frozen=True, slots=True)
class CapabilityGapHostContext:
    harness_version: str
    contract_identity: str
    contract_major: int
    correlation_id: str
    idempotency_fingerprint: str
    observed_at: datetime
    limitation_receipt: TrustedLimitationReceipt | None = None
    capability_comparison: CapabilityGapCapabilityComparison | None = None

    def __post_init__(self) -> None:
        # Reuse GAP-01 trusted validation without exposing a context codec.
        CapabilityGapWriteContext(
            self.harness_version,
            self.correlation_id,
            self.idempotency_fingerprint,
            self.observed_at,
            self.limitation_receipt,
        )
        if not isinstance(self.contract_identity, str) or not self.contract_identity:
            raise CapabilityGapValidationError("invalid_contract_identity")
        if type(self.contract_major) is not int or self.contract_major < 1:
            raise CapabilityGapValidationError("invalid_contract_major")
        if self.limitation_receipt is not None and (
            self.limitation_receipt.contract_identity != self.contract_identity
            or self.limitation_receipt.contract_major != self.contract_major
        ):
            raise CapabilityGapValidationError("limitation_receipt_context_mismatch")
        if self.capability_comparison is not None:
            if not isinstance(self.capability_comparison, CapabilityGapCapabilityComparison):
                raise CapabilityGapValidationError("invalid_capability_comparison")
            if (
                self.capability_comparison.contract_identity != self.contract_identity
                or self.capability_comparison.contract_major != self.contract_major
            ):
                raise CapabilityGapValidationError("capability_comparison_context_mismatch")
            if self.limitation_receipt is not None and (
                self.limitation_receipt != self.capability_comparison.limitation_receipt()
            ):
                raise CapabilityGapValidationError("limitation_receipt_context_mismatch")

    def to_write_context(self) -> CapabilityGapWriteContext:
        receipt = self.limitation_receipt
        if receipt is None and self.capability_comparison is not None:
            receipt = self.capability_comparison.limitation_receipt()
        return CapabilityGapWriteContext(
            self.harness_version,
            self.correlation_id,
            self.idempotency_fingerprint,
            self.observed_at,
            receipt,
        )


_PROPOSAL_SCHEMA: JsonObject = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": tuple(item.value for item in GapCategory),
        },
        "requested_operation_kind": {
            "type": "string",
            "enum": tuple(item.value for item in RequestedOperationKind),
        },
        "safe_target_kind": {
            "type": "string",
            "enum": tuple(item.value for item in SafeTargetKind),
        },
        "impact_kind": {
            "type": "string",
            "enum": tuple(item.value for item in ImpactKind),
        },
        "workaround_suggestion_kind": {
            "type": "string",
            "enum": tuple(item.value for item in WorkaroundSuggestionKind),
        },
    },
    "required": (
        "category",
        "requested_operation_kind",
        "safe_target_kind",
        "impact_kind",
    ),
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True, init=False)
class CapabilityGapHostToolManifest:
    identity: str
    description: str
    proposal_schema: JsonObject

    def __init__(self) -> None:
        """Construct only the canonical manifest; callers cannot substitute it."""

        schema = freeze_object(_PROPOSAL_SCHEMA)
        validate_schema_definition(schema)
        object.__setattr__(self, "identity", "report_capability_gap@1.0.0")
        object.__setattr__(
            self,
            "description",
            "Record a sanitized, local-only capability gap observation.",
        )
        object.__setattr__(self, "proposal_schema", schema)

    def to_json(self) -> JsonObject:
        return {
            "description": self.description,
            "identity": self.identity,
            "proposal_schema": self.proposal_schema,
        }


@dataclass(frozen=True, slots=True)
class CapabilityGapReportResult:
    report_id: str
    gap_key: GapKeyV1
    occurrence_count: int
    disposition: GapDisposition
    local_only: bool = True

    def __post_init__(self) -> None:
        if (
            not isinstance(self.report_id, str)
            or re.fullmatch(r"[0-9a-f]{64}", self.report_id) is None
        ):
            raise CapabilityGapValidationError("invalid_report_id")
        if not isinstance(self.gap_key, GapKeyV1):
            raise CapabilityGapValidationError("invalid_gap_key")
        if not isinstance(self.disposition, GapDisposition):
            raise CapabilityGapValidationError("invalid_disposition")
        if type(self.occurrence_count) is not int or self.occurrence_count < 1:
            raise CapabilityGapValidationError("invalid_occurrence_count")
        if self.local_only is not True:
            raise CapabilityGapValidationError("local_only_required")


class CapabilityGapHostTool:
    """Translate one closed model proposal into one trusted sink call."""

    manifest = CapabilityGapHostToolManifest()

    def __init__(
        self,
        sink: FeatureGapSink,
        *,
        dispatcher: CapabilityGapReportDispatcher | None = None,
    ) -> None:
        self._sink = sink
        self._dispatcher = dispatcher

    def report(
        self,
        proposal: CapabilityGapProposal,
        context: CapabilityGapHostContext,
    ) -> CapabilityGapReportResult:
        try:
            if not isinstance(proposal, CapabilityGapProposal):
                raise CapabilityGapValidationError("invalid_proposal")
            if not isinstance(context, CapabilityGapHostContext):
                raise CapabilityGapValidationError("invalid_host_context")
            if context.limitation_receipt is None and context.capability_comparison is None:
                raise CapabilityGapValidationError("unsupported_evidence_required")
            if context.capability_comparison is not None and (
                context.capability_comparison.requested_operation_kind
                is not proposal.requested_operation_kind
                or context.capability_comparison.safe_target_kind is not proposal.safe_target_kind
            ):
                raise CapabilityGapValidationError("capability_comparison_mismatch")
            observation = CapabilityGapObservation(
                proposal.category,
                proposal.requested_operation_kind,
                proposal.safe_target_kind,
                proposal.impact_kind,
            )
            compact = self._sink.record(observation, context.to_write_context())
            if not isinstance(compact, CapabilityGapCompactView) or compact.local_only is not True:
                raise CapabilityGapValidationError("invalid_sink_result")
            return CapabilityGapReportResult(
                compact.report_id,
                compact.gap_key,
                compact.occurrence_count,
                compact.disposition,
            )
        except Exception:
            raise CapabilityGapHostToolError("capability_gap_report_failed") from None

    def report_nonblocking(
        self,
        proposal: CapabilityGapProposal,
        context: CapabilityGapHostContext,
    ) -> None:
        """Try bounded host dispatch without touching sink/storage.

        The dispatcher is injected by the host and must implement a bounded,
        non-blocking ``try_submit``.  With no dispatcher this is an explicit
        fail-soft no-op; synchronous persistence remains available through
        :meth:`report`.
        """

        if not isinstance(proposal, CapabilityGapProposal) or not isinstance(
            context, CapabilityGapHostContext
        ):
            return None
        dispatcher = self._dispatcher
        if dispatcher is None:
            return None
        try:
            dispatcher.try_submit(proposal, context)
        except Exception:
            # Queue-full, closed, and host integration failures must never
            # interrupt the learner thread or fall back to synchronous I/O.
            return None
        return None


__all__ = [
    "CapabilityGapCapabilityComparison",
    "CapabilityGapHostContext",
    "CapabilityGapHostTool",
    "CapabilityGapHostToolError",
    "CapabilityGapHostToolManifest",
    "CapabilityGapProposal",
    "CapabilityGapReportResult",
    "WorkaroundSuggestionKind",
]
