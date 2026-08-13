from __future__ import annotations

import pytest

from cardine.feedback import (
    WorkaroundApprovalPolicy,
    WorkaroundApprovalReceipt,
    WorkaroundEffect,
    WorkaroundExecutionReceipt,
    WorkaroundGrant,
    WorkaroundInputKind,
    WorkaroundManifest,
    WorkaroundOutputKind,
    WorkaroundReceiptStatus,
    WorkaroundValidationError,
)


def _manifest() -> WorkaroundManifest:
    return WorkaroundManifest(
        "manual-derivative@1",
        1,
        WorkaroundInputKind.PDF,
        WorkaroundOutputKind.TEXT,
        (WorkaroundEffect.READ_LOCAL, WorkaroundEffect.WRITE_DERIVED),
        WorkaroundApprovalPolicy.HOST_APPROVAL,
        provenance_obligations=("source_digest",),
    )


def test_manifest_and_receipt_codecs_are_canonical_and_digest_bound() -> None:
    manifest = _manifest()
    assert WorkaroundManifest.from_bytes(manifest.to_bytes()) == manifest
    grant = WorkaroundGrant(manifest.identity, 1, manifest.effect_fingerprint)
    receipt = WorkaroundExecutionReceipt(
        WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED,
        manifest.identity,
        1,
        "a" * 64,
        "b" * 64,
        "c" * 64,
        manifest_fingerprint=manifest.fingerprint,
        effect_fingerprint=grant.effect_fingerprint,
        approval_fingerprint="d" * 64,
        executor_fingerprint="e" * 64,
    )
    assert WorkaroundExecutionReceipt.from_bytes(receipt.to_bytes()) == receipt
    approval = WorkaroundApprovalReceipt(
        "f" * 64,
        manifest.identity,
        manifest.version,
        manifest.fingerprint,
        manifest.effect_fingerprint,
        "d" * 64,
    )
    assert WorkaroundApprovalReceipt.from_bytes(approval.to_bytes()) == approval
    with pytest.raises(WorkaroundValidationError):
        WorkaroundManifest(
            "bad/path",
            1,
            WorkaroundInputKind.PDF,
            WorkaroundOutputKind.TEXT,
            (WorkaroundEffect.READ_LOCAL,),
            WorkaroundApprovalPolicy.NONE,
            provenance_obligations=("source_digest",),
        )
    with pytest.raises(WorkaroundValidationError):
        WorkaroundExecutionReceipt(
            WorkaroundReceiptStatus.ATTEMPTED_FAILED,
            manifest.identity,
            1,
            "a" * 64,
        )


def test_manifest_rejects_hidden_effects_and_dynamic_text() -> None:
    with pytest.raises(WorkaroundValidationError):
        WorkaroundManifest(
            "converter@1",
            1,
            WorkaroundInputKind.PDF,
            WorkaroundOutputKind.TEXT,
            (WorkaroundEffect.NETWORK,),
            WorkaroundApprovalPolicy.NONE,
            provenance_obligations=("source_digest",),
        )
