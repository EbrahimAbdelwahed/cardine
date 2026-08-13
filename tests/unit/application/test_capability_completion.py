from __future__ import annotations

import pytest

from cardine.application.capability_completion import (
    CapabilityCompletionHandlerRegistry,
    CapabilityCompletionProductReceipt,
)
from cardine.hosts import TutorCapabilityCompletionReference
from study_agent.domain import ExecutionContext, RunId


def _reference() -> TutorCapabilityCompletionReference:
    return TutorCapabilityCompletionReference(
        "explain_concept@1",
        "a" * 64,
        RunId("run-1"),
        "b" * 64,
        "c" * 64,
    )


class _Handler:
    def recover(
        self,
        reference: TutorCapabilityCompletionReference,
        context: ExecutionContext | None = None,
    ) -> CapabilityCompletionProductReceipt:
        del context
        return CapabilityCompletionProductReceipt(
            reference.capability_identity,
            reference.run_id,
            "Grounded explanation.",
            ("chunk-1",),
        )


class _OwnerRecovery:
    def recover(
        self,
        reference: TutorCapabilityCompletionReference,
        context: ExecutionContext | None = None,
    ) -> CapabilityCompletionProductReceipt | None:
        del context
        if reference.output_fingerprint != "b" * 64:
            return None
        return CapabilityCompletionProductReceipt(
            reference.capability_identity,
            reference.run_id,
            "Recovered output.",
        )


def test_unknown_completion_is_status_only() -> None:
    registry = CapabilityCompletionHandlerRegistry()
    assert registry.recover(_reference()) is None


def test_registry_requires_reference_bound_receipt() -> None:
    reference = _reference()
    registry = CapabilityCompletionHandlerRegistry(
        ((reference.capability_identity, reference.manifest_fingerprint, _Handler()),)
    )
    receipt = registry.recover(reference)
    assert receipt is not None
    assert receipt.run_id == reference.run_id
    assert receipt.canonical_ids == ("chunk-1",)


def test_unrecoverable_or_tampered_completion_is_status_only() -> None:
    reference = _reference()
    registry = CapabilityCompletionHandlerRegistry(
        ((reference.capability_identity, reference.manifest_fingerprint, _OwnerRecovery()),)
    )
    tampered = TutorCapabilityCompletionReference(
        reference.capability_identity,
        reference.manifest_fingerprint,
        reference.run_id,
        "d" * 64,
        reference.retry_receipt_fingerprint,
    )
    assert registry.recover(tampered) is None


def test_reference_rejects_non_sha256_identity_fields() -> None:
    with pytest.raises(ValueError):
        TutorCapabilityCompletionReference(
            "explain_concept@1", "bad", RunId("run-1"), "b" * 64, "c" * 64
        )
