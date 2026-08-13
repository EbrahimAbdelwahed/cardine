"""Provider-neutral inward seam for host-installed workaround execution receipts."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from cardine.feedback.workarounds import (
        WorkaroundApprovalReceipt,
        WorkaroundExecutionReceipt,
        WorkaroundGrant,
        WorkaroundManifest,
        WorkaroundTask,
    )


class WorkaroundExecutor(Protocol):
    """A host may inject an executor; the core never implements or discovers one."""

    def execute(
        self, task: WorkaroundTask, manifest_identity: str
    ) -> WorkaroundExecutionReceipt: ...


class WorkaroundApprovalAuthority(Protocol):
    """Trusted host seam for approval; model/caller inputs never authorize."""

    def approve(
        self,
        task: WorkaroundTask,
        manifest: WorkaroundManifest,
        grant: WorkaroundGrant,
    ) -> WorkaroundApprovalReceipt | None: ...


__all__ = ["WorkaroundApprovalAuthority", "WorkaroundExecutor"]
