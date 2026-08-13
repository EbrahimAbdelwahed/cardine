"""Narrow ports for verified artifact writes and projection-only reads."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from study_agent.artifacts.content import StudyArtifactEnvelope
    from study_agent.artifacts.contracts import (
        ArtifactBulkDecisionReceipt,
        ArtifactDecisionRequest,
        ArtifactSnapshot,
        ServiceDecisionPolicyReceipt,
        ServiceDecisionPolicyRequest,
        VerifiedGeneratedArtifactBatch,
    )
    from study_agent.artifacts.identity import HumanAuthoredArtifactProvenance
    from study_agent.domain import (
        ArtifactDecision,
        ArtifactId,
        ArtifactRevisionId,
        CourseId,
        EventId,
        ExecutionContext,
        RunId,
        SourceCommitment,
    )


class ArtifactCommandPort(Protocol):
    """The typed host seam for generic artifact lifecycle commands."""

    def record_generated(
        self, run_id: RunId, context: ExecutionContext, expected_sequence: int
    ) -> ArtifactSnapshot: ...

    def record_human_revision(
        self,
        content: StudyArtifactEnvelope,
        provenance: HumanAuthoredArtifactProvenance,
        target_artifact_id: ArtifactId | None,
        context: ExecutionContext,
        expected_sequence: int,
    ) -> ArtifactSnapshot: ...

    def record_human_decision(
        self,
        revision_id: ArtifactRevisionId,
        decision: ArtifactDecision,
        supersedes_revision_id: ArtifactRevisionId | None,
        context: ExecutionContext,
        expected_sequence: int,
    ) -> ArtifactSnapshot: ...

    def record_human_decision_batch(
        self,
        decisions: tuple[ArtifactDecisionRequest, ...],
        context: ExecutionContext,
        expected_sequence: int,
    ) -> ArtifactBulkDecisionReceipt: ...

    def record_human_decisions(
        self,
        decisions: tuple[ArtifactDecisionRequest, ...],
        context: ExecutionContext,
        expected_sequence: int,
    ) -> ArtifactBulkDecisionReceipt: ...

    def apply_service_decision(
        self,
        revision_id: ArtifactRevisionId,
        context: ExecutionContext,
        expected_sequence: int,
    ) -> ArtifactSnapshot: ...


class VerifiedGeneratedBatchPort(Protocol):
    def recover(
        self, run_id: RunId, context: ExecutionContext
    ) -> VerifiedGeneratedArtifactBatch: ...


class SourceCommitmentLookupPort(Protocol):
    def contains(self, course_id: CourseId, commitment: SourceCommitment) -> bool: ...


class ServiceDecisionPolicyPort(Protocol):
    """Deterministic/idempotent policy keyed by ``request.request_id``."""

    def decide(self, request: ServiceDecisionPolicyRequest) -> ServiceDecisionPolicyReceipt: ...


class ArtifactViewPort(Protocol):
    def get(self, course_id: CourseId) -> ArtifactSnapshot: ...

    def command_fingerprint(self, course_id: CourseId, event_id: EventId) -> str | None: ...


__all__ = [
    "ArtifactCommandPort",
    "ArtifactViewPort",
    "ServiceDecisionPolicyPort",
    "SourceCommitmentLookupPort",
    "VerifiedGeneratedBatchPort",
]
