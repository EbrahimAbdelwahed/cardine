from __future__ import annotations

import pytest

from cardine.feedback import (
    WorkaroundApprovalPolicy,
    WorkaroundApprovalReceipt,
    WorkaroundAuthorityError,
    WorkaroundEffect,
    WorkaroundExecutionReceipt,
    WorkaroundGrant,
    WorkaroundInputKind,
    WorkaroundManifest,
    WorkaroundOutputKind,
    WorkaroundReceiptStatus,
    WorkaroundRegistry,
    WorkaroundTask,
    WorkaroundValidationError,
)


def _registry() -> tuple[WorkaroundRegistry, WorkaroundManifest, WorkaroundTask, WorkaroundGrant]:
    manifest = WorkaroundManifest(
        "manual@1",
        1,
        WorkaroundInputKind.PDF,
        WorkaroundOutputKind.TEXT,
        (WorkaroundEffect.READ_LOCAL,),
        WorkaroundApprovalPolicy.HOST_APPROVAL,
        provenance_obligations=("source_digest",),
    )
    task = WorkaroundTask(WorkaroundInputKind.PDF, WorkaroundOutputKind.TEXT, "a" * 64)
    grant = WorkaroundGrant(manifest.identity, 1, manifest.effect_fingerprint)
    return WorkaroundRegistry((manifest,)), manifest, task, grant


def test_selection_requires_installed_grant_and_approval() -> None:
    registry, _manifest, task, grant = _registry()
    assert registry.select(task, frozenset()).manifest_identity == "none@1"
    selected = registry.select(task, (grant,))
    assert selected.status is WorkaroundReceiptStatus.REQUIRES_APPROVAL
    with pytest.raises(WorkaroundValidationError):
        registry.validate_execution(task, selected, granted=(grant,))


def test_attempted_receipt_must_match_manifest_effect_and_provenance() -> None:
    registry, manifest, task, grant = _registry()
    receipt = WorkaroundExecutionReceipt(
        WorkaroundReceiptStatus.ATTEMPTED_FAILED,
        manifest.identity,
        1,
        "a" * 64,
        limitation_fingerprint="b" * 64,
        manifest_fingerprint=manifest.fingerprint,
        effect_fingerprint=manifest.effect_fingerprint,
        executor_fingerprint="c" * 64,
    )
    approval = WorkaroundApprovalReceipt(
        task.fingerprint,
        manifest.identity,
        manifest.version,
        manifest.fingerprint,
        manifest.effect_fingerprint,
        "d" * 64,
    )
    receipt = WorkaroundExecutionReceipt(
        receipt.status,
        receipt.manifest_identity,
        receipt.manifest_version,
        receipt.input_fingerprint,
        receipt.output_fingerprint,
        receipt.provenance_fingerprint,
        receipt.limitation_fingerprint,
        receipt.manifest_fingerprint,
        receipt.effect_fingerprint,
        approval.approval_fingerprint,
        "e" * 64,
    )
    assert (
        registry.validate_execution(task, receipt, granted=(grant,), approval=approval)
        == receipt
    )
    with pytest.raises(WorkaroundAuthorityError):
        registry.validate_execution(task, receipt, granted=(), approval=approval)
