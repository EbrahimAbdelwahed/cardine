"""Unsupported-source-format tracer bullet.

The tracer accepts typed host evidence only.  It never receives or inspects a
filename, path, MIME string, source body, or prompt text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from .contracts import (
    CapabilityGapObservation,
    CapabilityGapWriteContext,
    GapCategory,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    TrustedLimitationCode,
    TrustedLimitationReceipt,
)
from .service import CapabilityGapService


class SourceFormatDisposition(StrEnum):
    NOT_INGESTED = "not_ingested"
    SUPPORTED_DERIVATIVE_REQUESTED = "supported_derivative_requested"


_EXTENSION = re.compile(r"^\.[A-Za-z0-9]{1,16}$")
_MIME = re.compile(r"^[a-z0-9][a-z0-9.+-]{0,30}/[a-z0-9][a-z0-9.+-]{0,30}$")


@dataclass(frozen=True, slots=True)
class SourceFormatMetadata:
    """Small host-produced metadata record; source bodies are not a field."""

    extension: str
    mime_type: str | None = None
    filename: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.extension, str) or _EXTENSION.fullmatch(self.extension) is None:
            raise ValueError("invalid_source_extension")
        if self.mime_type is not None and (
            not isinstance(self.mime_type, str) or _MIME.fullmatch(self.mime_type.lower()) is None
        ):
            raise ValueError("invalid_source_mime")
        if self.filename is not None:
            if not isinstance(self.filename, str) or not self.filename:
                raise ValueError("invalid_source_filename")
            # A filename is never persisted.  Reject paths and control text so
            # hostile names cannot become policy/input instructions.
            if (
                "/" in self.filename
                or "\\" in self.filename
                or ".." in self.filename
                or any(ord(char) < 32 for char in self.filename)
            ):
                raise ValueError("invalid_source_filename")


@dataclass(frozen=True, slots=True)
class UnsupportedSourceEvidence:
    """Host-produced evidence for an unsupported source family."""

    target_kind: SafeTargetKind
    contract_identity: str
    contract_major: int
    failure_fingerprint: str
    extension: str | None = None
    mime_type: str | None = None

    def __post_init__(self) -> None:
        # Metadata is checked only for shape.  It is deliberately omitted from
        # the operational report and never used to read a body or filename.
        if self.extension is not None or self.mime_type is not None:
            SourceFormatMetadata(self.extension or ".unknown", self.mime_type)
        if not isinstance(self.target_kind, SafeTargetKind):
            raise ValueError("invalid_source_target")

@dataclass(frozen=True, slots=True)
class SourceFormatTrace:
    disposition: SourceFormatDisposition
    learner_message: str
    report: object
    original_immutable: bool = True
    derivative_kinds: tuple[SafeTargetKind, ...] = (SafeTargetKind.TEXT, SafeTargetKind.MARKDOWN)


def trace_unsupported_source_format(
    service: CapabilityGapService,
    evidence: UnsupportedSourceEvidence,
    context: CapabilityGapWriteContext,
    metadata: SourceFormatMetadata | None = None,
) -> SourceFormatTrace:
    """Record one typed limitation and return an honest manual fallback."""

    if not isinstance(evidence, UnsupportedSourceEvidence):
        raise ValueError("invalid_source_evidence")
    if metadata is not None and not isinstance(metadata, SourceFormatMetadata):
        raise ValueError("invalid_source_metadata")
    # Evidence is a comparison view, never an authority factory.  Only the
    # host-trusted receipt already bound into the write context may authorize
    # persistence; absence and mismatch both fail closed before ``record``.
    trusted_receipt = context.limitation_receipt
    if trusted_receipt is None:
        raise ValueError("limitation_receipt_required")
    expected_receipt = TrustedLimitationReceipt(
        evidence.contract_identity,
        evidence.contract_major,
        TrustedLimitationCode.UNSUPPORTED_FORMAT,
        evidence.failure_fingerprint,
    )
    if trusted_receipt != expected_receipt:
        raise ValueError("limitation_receipt_context_mismatch")
    write_context = context
    observation = CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.INGEST_SOURCE,
        evidence.target_kind,
        ImpactKind.WORKAROUND_AVAILABLE,
    )
    receipt = service.record(observation, write_context)
    return SourceFormatTrace(
        SourceFormatDisposition.SUPPORTED_DERIVATIVE_REQUESTED,
        (
            "I couldn't ingest this material format. Please provide a supported "
            ".txt or .md derivative; the original was not changed."
        ),
        receipt,
        original_immutable=True,
    )


__all__ = [
    "SourceFormatDisposition",
    "SourceFormatMetadata",
    "SourceFormatTrace",
    "UnsupportedSourceEvidence",
    "trace_unsupported_source_format",
]
