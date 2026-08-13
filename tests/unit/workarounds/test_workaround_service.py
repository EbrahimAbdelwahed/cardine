from __future__ import annotations

from dataclasses import dataclass

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
    WorkaroundService,
    WorkaroundTask,
    WorkaroundValidationError,
)


def _fixture() -> tuple[WorkaroundRegistry, WorkaroundManifest, WorkaroundTask, WorkaroundGrant]:
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
    return WorkaroundRegistry((manifest,)), manifest, task, WorkaroundGrant(
        manifest.identity, manifest.version, manifest.effect_fingerprint
    )


@dataclass
class _Executor:
    receipt: WorkaroundExecutionReceipt
    calls: int = 0

    def execute(self, task: WorkaroundTask, manifest_identity: str) -> WorkaroundExecutionReceipt:
        self.calls += 1
        return self.receipt


@dataclass
class _Authority:
    approval: WorkaroundApprovalReceipt | None
    calls: int = 0

    def approve(
        self,
        task: WorkaroundTask,
        manifest: WorkaroundManifest,
        grant: WorkaroundGrant,
    ) -> WorkaroundApprovalReceipt | None:
        self.calls += 1
        return self.approval


def _approval(task: WorkaroundTask, manifest: WorkaroundManifest) -> WorkaroundApprovalReceipt:
    return WorkaroundApprovalReceipt(
        task.fingerprint,
        manifest.identity,
        manifest.version,
        manifest.fingerprint,
        manifest.effect_fingerprint,
        "d" * 64,
    )


def _receipt(
    manifest: WorkaroundManifest,
    task: WorkaroundTask,
    approval: str | None,
) -> WorkaroundExecutionReceipt:
    return WorkaroundExecutionReceipt(
        WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED,
        manifest.identity,
        manifest.version,
        task.input_fingerprint,
        "b" * 64,
        "c" * 64,
        manifest_fingerprint=manifest.fingerprint,
        effect_fingerprint=manifest.effect_fingerprint,
        approval_fingerprint=approval,
        executor_fingerprint="e" * 64,
    )


def test_service_requires_authority_and_executor_before_effect() -> None:
    registry, _manifest, task, grant = _fixture()
    with pytest.raises(WorkaroundAuthorityError, match="executor_not_configured"):
        WorkaroundService(registry).record_execution(task, grants=(grant,))

    executor = _Executor(_receipt(_manifest, task, None))
    with pytest.raises(WorkaroundAuthorityError, match="approval_required"):
        WorkaroundService(registry, executor=executor).record_execution(task, grants=(grant,))
    assert executor.calls == 0


def test_matching_host_approval_executes_once_and_binds_receipt() -> None:
    registry, manifest, task, grant = _fixture()
    approval = _approval(task, manifest)
    executor = _Executor(_receipt(manifest, task, approval.approval_fingerprint))
    authority = _Authority(approval)

    result = WorkaroundService(
        registry, executor=executor, approval_authority=authority
    ).record_execution(task, grants=(grant,))

    assert result.status is WorkaroundReceiptStatus.ATTEMPTED_SUCCEEDED
    assert executor.calls == 1
    assert authority.calls == 1


def test_forged_executor_receipt_is_rejected_after_exactly_one_call() -> None:
    registry, manifest, task, grant = _fixture()
    approval = _approval(task, manifest)
    forged = _receipt(manifest, task, "f" * 64)
    executor = _Executor(forged)
    authority = _Authority(approval)

    with pytest.raises(WorkaroundValidationError, match="approval_fingerprint_mismatch"):
        WorkaroundService(
            registry, executor=executor, approval_authority=authority
        ).record_execution(task, grants=(grant,))
    assert executor.calls == 1


def test_selection_never_executes() -> None:
    registry, manifest, task, grant = _fixture()
    executor = _Executor(_receipt(manifest, task, None))
    service = WorkaroundService(registry, executor=executor)
    selected = service.select(task, (grant,))
    assert selected.status is WorkaroundReceiptStatus.REQUIRES_APPROVAL
    assert executor.calls == 0
