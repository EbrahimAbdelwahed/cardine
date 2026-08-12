from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from typing import cast

import pytest

import cardine.feedback as feedback
from cardine.feedback import (
    CapabilityGapAggregate,
    CapabilityGapCollisionError,
    CapabilityGapCorruptionError,
    CapabilityGapDimensions,
    CapabilityGapObservation,
    CapabilityGapValidationError,
    CapabilityGapWriteContext,
    GapCategory,
    GapKeyV1,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    TrustedLimitationCode,
    TrustedLimitationReceipt,
    VerificationKind,
    proposal_for,
    report_id_for,
)
from study_agent.domain._validation import JsonObject
from study_agent.state import canonical_json_bytes

SHA_A = "a" * 64
SHA_B = "b" * 64
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def _observation(
    operation: RequestedOperationKind = RequestedOperationKind.EXTRACT_TEXT,
) -> CapabilityGapObservation:
    return CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        operation,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
    )


def _context(
    fingerprint: str = SHA_A,
    *,
    receipt: TrustedLimitationReceipt | None = None,
) -> CapabilityGapWriteContext:
    return CapabilityGapWriteContext("harness@1", "corr@1", fingerprint, NOW, receipt)


def test_closed_contracts_are_canonical_and_domain_separated() -> None:
    observation = _observation()
    assert CapabilityGapObservation.from_bytes(observation.to_bytes()) == observation
    dimensions = proposal_for(observation, _context()).dimensions
    expected_dimensions = canonical_json_bytes(cast(JsonObject, dimensions.to_json()))
    assert dimensions.to_bytes() == expected_dimensions
    key = GapKeyV1.derive(dimensions)
    assert key.value == sha256(b"study-agent-gap-key-v1\0" + expected_dimensions).hexdigest()
    assert key == GapKeyV1.from_dimensions(dimensions)

    aggregate = proposal_for(observation, _context())
    assert CapabilityGapAggregate.from_bytes(aggregate.to_bytes()) == aggregate
    report = report_id_for(key, SHA_A)
    assert report == sha256(
        b"study-agent-gap-report-v1\0" + key.value.encode() + b"\0" + SHA_A.encode()
    ).hexdigest()


def test_unverifiable_report_envelope_is_not_a_public_codec() -> None:
    # A verified report needs trusted limitation/contract inputs that are not
    # present in a portable envelope.  Keep the unsafe decoder out of the API.
    assert not hasattr(feedback, "CapabilityGapReport")


def test_pdf_extract_and_table_preservation_have_distinct_gap_keys() -> None:
    extract = proposal_for(_observation(), _context())
    tables = proposal_for(
        _observation(RequestedOperationKind.PRESERVE_TABLES), _context()
    )
    assert extract.gap_key != tables.gap_key
    assert extract.dimensions.safe_target_kind is SafeTargetKind.PDF
    assert tables.dimensions.requested_operation_kind is RequestedOperationKind.PRESERVE_TABLES


def test_receipt_controls_verified_limitation_and_is_not_model_payload() -> None:
    receipt = TrustedLimitationReceipt(
        "extractor@2",
        2,
        TrustedLimitationCode.UNSUPPORTED_FORMAT,
        SHA_B,
    )
    assert TrustedLimitationReceipt.from_bytes(receipt.to_bytes()) == receipt
    verified = proposal_for(_observation(), _context(receipt=receipt))
    assert verified.verification_kind is VerificationKind.VERIFIED_RUNTIME_FAILURE
    assert verified.dimensions.limitation_code is TrustedLimitationCode.UNSUPPORTED_FORMAT
    unverified = proposal_for(_observation(), _context())
    assert unverified.verification_kind is VerificationKind.UNVERIFIED_REQUEST
    assert unverified.dimensions.limitation_code is TrustedLimitationCode.MISSING_CAPABILITY
    assert receipt.to_bytes().find(b"prompt") == -1


@pytest.mark.parametrize(
    ("factory", "value"),
    (
        (
            lambda value: CapabilityGapWriteContext("harness@1", "corr@1", value, NOW),
            "not-a-digest",
        ),
        (
            lambda value: TrustedLimitationReceipt(
                value, 1, TrustedLimitationCode.MISSING_CAPABILITY, SHA_A
            ),
            "/tmp/path",
        ),
        (
            lambda value: TrustedLimitationReceipt(
                "contract@1", value, TrustedLimitationCode.MISSING_CAPABILITY, SHA_A
            ),
            True,
        ),
        (
            lambda value: CapabilityGapDimensions(
                1,
                GapCategory.INPUT_FORMAT,
                RequestedOperationKind.EXTRACT_TEXT,
                SafeTargetKind.PDF,
                TrustedLimitationCode.MISSING_CAPABILITY,
                "contract@1",
                value,
            ),
            True,
        ),
    ),
)
def test_trusted_context_rejects_unbounded_or_forged_values(factory: object, value: object) -> None:
    with pytest.raises(CapabilityGapValidationError):
        factory(value)  # type: ignore[operator]


def test_codecs_reject_unknown_fields_noncanonical_bytes_and_key_tampering() -> None:
    observation = _observation()
    extra = observation.to_json()
    extra["source_body"] = "secret learner text"
    with pytest.raises(CapabilityGapCorruptionError):
        CapabilityGapObservation.from_bytes(canonical_json_bytes(cast(JsonObject, extra)))
    with pytest.raises(CapabilityGapCorruptionError, match="noncanonical"):
        CapabilityGapObservation.from_bytes(observation.to_bytes().replace(b",", b", ", 1))

    aggregate = proposal_for(observation, _context())
    forged = aggregate.to_json()
    forged["gap_key"] = SHA_B
    with pytest.raises(CapabilityGapCollisionError):
        CapabilityGapAggregate.from_bytes(canonical_json_bytes(cast(JsonObject, forged)))
    forged_count = aggregate.to_json()
    forged_count["occurrence_count"] = True
    with pytest.raises(CapabilityGapCorruptionError):
        CapabilityGapAggregate.from_bytes(
            canonical_json_bytes(cast(JsonObject, forged_count))
        )
