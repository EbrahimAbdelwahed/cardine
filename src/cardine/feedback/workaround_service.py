"""Selection and receipt validation boundary for static workaround manifests."""

from __future__ import annotations

from study_agent.ports.workaround import WorkaroundApprovalAuthority, WorkaroundExecutor

from .workarounds import (
    WorkaroundApprovalPolicy,
    WorkaroundAuthorityError,
    WorkaroundExecutionReceipt,
    WorkaroundGrant,
    WorkaroundRegistry,
    WorkaroundTask,
)


class WorkaroundService:
    """Selection is inert; execution requires host-composed inward ports."""

    def __init__(
        self,
        registry: WorkaroundRegistry,
        *,
        executor: WorkaroundExecutor | None = None,
        approval_authority: WorkaroundApprovalAuthority | None = None,
    ) -> None:
        self._registry = registry
        self._executor = executor
        self._approval_authority = approval_authority

    def select(
        self, task: WorkaroundTask, grants: frozenset[str] | tuple[WorkaroundGrant, ...]
    ) -> WorkaroundExecutionReceipt:
        return self._registry.select(task, grants)

    def record_execution(
        self,
        task: WorkaroundTask,
        *,
        grants: frozenset[str] | tuple[WorkaroundGrant, ...],
    ) -> WorkaroundExecutionReceipt:
        if self._executor is None:
            raise WorkaroundAuthorityError("executor_not_configured")
        manifest, grant = self._registry.resolve_execution(task, grants)
        approval = None
        if (
            manifest.approval_policy is WorkaroundApprovalPolicy.HOST_APPROVAL
            and self._approval_authority is not None
        ):
            approval = self._approval_authority.approve(task, manifest, grant)
        self._registry.validate_approval(task, manifest, grant, approval)
        receipt = self._executor.execute(task, manifest.identity)
        return self._registry.validate_execution(
            task, receipt, granted=grants, approval=approval
        )


__all__ = ["WorkaroundService"]
