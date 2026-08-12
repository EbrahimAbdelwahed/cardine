"""Application-layer contracts for canonical study-artifact lifecycle state."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from types import MappingProxyType

from study_agent.domain import (
    ArtifactBatchId,
    ArtifactDecision,
    ArtifactId,
    ArtifactRevisionId,
    ArtifactRevisionStatus,
    CourseId,
    EventId,
    RunId,
    SessionId,
    StudyArtifactKind,
)
from study_agent.domain._validation import require_aware, require_text
from study_agent.state import canonical_json_bytes

from .content import StudyArtifactEnvelope
from .identity import (
    ArtifactProvenance,
    GeneratedArtifactProvenance,
    HumanAuthoredArtifactProvenance,
)


class ArtifactProposalOrigin(StrEnum):
    GENERATED = "generated"
    HUMAN_AUTHORED = "human_authored"


@dataclass(frozen=True, slots=True)
class ArtifactProposal:
    ordinal: int
    content: StudyArtifactEnvelope
    provenance: ArtifactProvenance
    target_artifact_id: ArtifactId | None = None

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("artifact proposal ordinal must be non-negative")
        if not isinstance(self.content, StudyArtifactEnvelope):
            raise TypeError("artifact proposal content must be StudyArtifactEnvelope")
        if not isinstance(
            self.provenance,
            (GeneratedArtifactProvenance, HumanAuthoredArtifactProvenance),
        ):
            raise TypeError("artifact proposal provenance is invalid")
        if self.target_artifact_id is not None and not isinstance(
            self.target_artifact_id, ArtifactId
        ):
            raise TypeError("target_artifact_id must be ArtifactId or absent")


@dataclass(frozen=True, slots=True)
class GeneratedBatchProofReceipt:
    verifier_id: str
    verifier_version: str
    verifier_fingerprint: str

    def __post_init__(self) -> None:
        require_text(self.verifier_id, "generated batch verifier_id")
        require_text(self.verifier_version, "generated batch verifier_version")
        if re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", self.verifier_id) is None:
            raise ValueError("generated batch verifier_id must be portable lowercase text")
        if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", self.verifier_version) is None:
            raise ValueError("generated batch verifier_version must be portable")
        _fingerprint(self.verifier_fingerprint, "generated batch verifier_fingerprint")


@dataclass(frozen=True, slots=True)
class VerifiedGeneratedArtifactBatch:
    run_id: RunId
    course_id: CourseId
    session_id: SessionId
    proposals: tuple[ArtifactProposal, ...]
    proof: GeneratedBatchProofReceipt

    def __post_init__(self) -> None:
        if not isinstance(self.run_id, RunId):
            raise TypeError("verified generated batch run_id must be RunId")
        if not isinstance(self.course_id, CourseId) or not isinstance(self.session_id, SessionId):
            raise TypeError("verified generated batch requires typed course and session ids")
        proposals = tuple(self.proposals)
        if not 1 <= len(proposals) <= 24:
            raise ValueError("artifact proposal batch must contain 1..24 proposals")
        if tuple(item.ordinal for item in proposals) != tuple(range(len(proposals))):
            raise ValueError("artifact proposal ordinals must be contiguous from zero")
        if any(
            not isinstance(item.provenance, GeneratedArtifactProvenance)
            or item.provenance.run_id != self.run_id
            for item in proposals
        ):
            raise ValueError("verified batch requires generated provenance for its exact run")
        targets = tuple(
            item.target_artifact_id for item in proposals if item.target_artifact_id is not None
        )
        if len(set(targets)) != len(targets):
            raise ValueError("verified batch cannot revise one artifact twice")
        object.__setattr__(self, "proposals", proposals)


@dataclass(frozen=True, slots=True)
class ServiceDecisionPolicyRequest:
    request_id: str
    course_id: CourseId
    session_id: SessionId
    revision_id: ArtifactRevisionId
    kind: StudyArtifactKind
    current_accepted_revision_id: ArtifactRevisionId | None

    def __post_init__(self) -> None:
        require_text(self.request_id, "policy request_id")


@dataclass(frozen=True, slots=True)
class ServiceDecisionPolicyReceipt:
    request_id: str
    decision: ArtifactDecision
    supersedes_revision_id: ArtifactRevisionId | None
    policy_id: str
    policy_version: str
    policy_fingerprint: str
    result_fingerprint: str

    def __post_init__(self) -> None:
        require_text(self.request_id, "policy receipt request_id")
        if not isinstance(self.decision, ArtifactDecision):
            raise TypeError("policy decision must use ArtifactDecision")
        require_text(self.policy_id, "policy_id")
        require_text(self.policy_version, "policy_version")
        if re.fullmatch(r"[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*", self.policy_id) is None:
            raise ValueError("policy_id must be a portable lowercase identifier")
        if re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]*", self.policy_version) is None:
            raise ValueError("policy_version must be portable")
        _fingerprint(self.policy_fingerprint, "policy_fingerprint")
        _fingerprint(self.result_fingerprint, "result_fingerprint")
        receipt_text = " ".join((self.policy_id, self.policy_version)).lower()
        if any(
            token in receipt_text
            for token in (
                "api_key",
                "access_token",
                "password",
                "secret",
                "bearer ",
                "token=",
            )
        ):
            raise ValueError("policy receipt cannot contain secret-shaped values")
        if self.decision is ArtifactDecision.REJECT and self.supersedes_revision_id is not None:
            raise ValueError("reject policy receipt cannot supersede a revision")


def service_decision_result_fingerprint(
    request: ServiceDecisionPolicyRequest,
    decision: ArtifactDecision,
    supersedes_revision_id: ArtifactRevisionId | None,
) -> str:
    return sha256(
        canonical_json_bytes(
            {
                "request_id": request.request_id,
                "course_id": str(request.course_id),
                "session_id": str(request.session_id),
                "revision_id": str(request.revision_id),
                "kind": request.kind.value,
                "current_accepted_revision_id": (
                    str(request.current_accepted_revision_id)
                    if request.current_accepted_revision_id
                    else None
                ),
                "decision": decision.value,
                "supersedes_revision_id": (
                    str(supersedes_revision_id) if supersedes_revision_id else None
                ),
            }
        )
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class ArtifactRevisionRecord:
    id: ArtifactRevisionId
    artifact_id: ArtifactId
    batch_id: ArtifactBatchId
    ordinal: int
    kind: StudyArtifactKind
    status: ArtifactRevisionStatus
    content: StudyArtifactEnvelope
    provenance: ArtifactProvenance
    prior_revision_id: ArtifactRevisionId | None
    parent_artifact_id: ArtifactId | None
    proposed_at: datetime
    decided_at: datetime | None = None

    def __post_init__(self) -> None:
        require_aware(self.proposed_at, "artifact proposed_at")
        if self.decided_at is not None:
            require_aware(self.decided_at, "artifact decided_at")


@dataclass(frozen=True, slots=True)
class ArtifactBatchRecord:
    id: ArtifactBatchId
    course_id: CourseId
    session_id: SessionId
    origin: ArtifactProposalOrigin
    revision_ids: tuple[ArtifactRevisionId, ...]
    run_id: RunId | None
    recorded_at: datetime

    def __post_init__(self) -> None:
        if not isinstance(self.origin, ArtifactProposalOrigin):
            raise TypeError("artifact batch origin is invalid")
        revisions = tuple(self.revision_ids)
        if not 1 <= len(revisions) <= 24 or len(set(revisions)) != len(revisions):
            raise ValueError("artifact batch revision ids must be bounded and unique")
        object.__setattr__(self, "revision_ids", revisions)
        require_aware(self.recorded_at, "artifact batch recorded_at")
        if (self.origin is ArtifactProposalOrigin.GENERATED) != (self.run_id is not None):
            raise ValueError("only generated artifact batches carry a run id")


@dataclass(frozen=True, slots=True)
class ArtifactDecisionRecord:
    revision_id: ArtifactRevisionId
    decision: ArtifactDecision
    supersedes_revision_id: ArtifactRevisionId | None
    decided_at: datetime
    policy_receipt: ServiceDecisionPolicyReceipt | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.decision, ArtifactDecision):
            raise TypeError("artifact decision record decision is invalid")
        require_aware(self.decided_at, "artifact decision decided_at")
        if self.decision is ArtifactDecision.REJECT and self.supersedes_revision_id is not None:
            raise ValueError("rejected artifact decision cannot supersede")
        if self.policy_receipt is not None and (
            self.policy_receipt.decision is not self.decision
            or self.policy_receipt.supersedes_revision_id != self.supersedes_revision_id
        ):
            raise ValueError("policy receipt does not bind artifact decision record")


@dataclass(frozen=True, slots=True)
class ArtifactDecisionRequest:
    """One ordered HUMAN decision in an atomic bulk request."""

    revision_id: ArtifactRevisionId
    decision: ArtifactDecision
    supersedes_revision_id: ArtifactRevisionId | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.revision_id, ArtifactRevisionId):
            raise TypeError("artifact decision revision_id is invalid")
        if not isinstance(self.decision, ArtifactDecision):
            raise TypeError("artifact decision decision is invalid")
        if self.supersedes_revision_id is not None and not isinstance(
            self.supersedes_revision_id, ArtifactRevisionId
        ):
            raise TypeError("artifact decision predecessor is invalid")
        if self.decision is ArtifactDecision.REJECT and self.supersedes_revision_id is not None:
            raise ValueError("reject never supersedes")


@dataclass(frozen=True, slots=True)
class ArtifactDecisionResult:
    """The durable result for one item in an atomic decision receipt."""

    ordinal: int
    revision_id: ArtifactRevisionId
    decision: ArtifactDecision
    supersedes_revision_id: ArtifactRevisionId | None
    event_id: EventId
    sequence: int

    def __post_init__(self) -> None:
        if type(self.ordinal) is not int or self.ordinal < 0:
            raise ValueError("artifact decision result ordinal must be non-negative")
        if not isinstance(self.revision_id, ArtifactRevisionId):
            raise TypeError("artifact decision result revision_id is invalid")
        if not isinstance(self.decision, ArtifactDecision):
            raise TypeError("artifact decision result decision is invalid")
        if self.supersedes_revision_id is not None and not isinstance(
            self.supersedes_revision_id, ArtifactRevisionId
        ):
            raise TypeError("artifact decision result predecessor is invalid")
        if not isinstance(self.event_id, EventId):
            raise TypeError("artifact decision result event_id is invalid")
        if type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("artifact decision result sequence must be positive")


@dataclass(frozen=True, slots=True)
class ArtifactBulkDecisionReceipt:
    """Restart-stable receipt for one atomically appended HUMAN batch."""

    bulk_key: str
    course_id: CourseId
    session_id: SessionId
    request_fingerprint: str
    manifest_fingerprint: str
    start_sequence: int
    end_sequence: int
    results: tuple[ArtifactDecisionResult, ...]

    def __post_init__(self) -> None:
        require_text(self.bulk_key, "artifact bulk key")
        if not isinstance(self.course_id, CourseId) or not isinstance(self.session_id, SessionId):
            raise TypeError("artifact bulk receipt requires typed course/session")
        _fingerprint(self.request_fingerprint, "artifact bulk request_fingerprint")
        _fingerprint(self.manifest_fingerprint, "artifact bulk manifest_fingerprint")
        if type(self.start_sequence) is not int or type(self.end_sequence) is not int:
            raise TypeError("artifact bulk receipt sequences must be integers")
        if self.start_sequence < 1 or self.end_sequence < self.start_sequence:
            raise ValueError("artifact bulk receipt sequence range is invalid")
        results = tuple(self.results)
        if not 1 <= len(results) <= 24:
            raise ValueError("artifact bulk receipt requires 1..24 results")
        if tuple(item.ordinal for item in results) != tuple(range(len(results))):
            raise ValueError("artifact bulk receipt ordinals must be contiguous")
        if tuple(item.sequence for item in results) != tuple(
            range(self.start_sequence, self.end_sequence + 1)
        ):
            raise ValueError("artifact bulk receipt sequences are not contiguous")
        if self.end_sequence - self.start_sequence + 1 != len(results):
            raise ValueError("artifact bulk receipt range does not match results")
        object.__setattr__(self, "results", results)


@dataclass(frozen=True, slots=True)
class ArtifactSnapshot:
    course_id: CourseId
    sequence: int
    batches: tuple[ArtifactBatchRecord, ...] = ()
    revisions: tuple[ArtifactRevisionRecord, ...] = ()
    decisions: tuple[ArtifactDecisionRecord, ...] = ()
    _by_revision: Mapping[ArtifactRevisionId, ArtifactRevisionRecord] = field(
        init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "batches", tuple(self.batches))
        object.__setattr__(self, "revisions", tuple(self.revisions))
        object.__setattr__(self, "decisions", tuple(self.decisions))
        by_revision = {item.id: item for item in self.revisions}
        if len(by_revision) != len(self.revisions):
            raise ValueError("artifact snapshot cannot contain duplicate revisions")
        object.__setattr__(self, "_by_revision", MappingProxyType(by_revision))

    def revision(self, revision_id: ArtifactRevisionId) -> ArtifactRevisionRecord:
        try:
            return self._by_revision[revision_id]
        except KeyError as error:
            raise LookupError(f"artifact revision {revision_id} was not found") from error

    def history(self, artifact_id: ArtifactId) -> tuple[ArtifactRevisionRecord, ...]:
        return tuple(item for item in self.revisions if item.artifact_id == artifact_id)

    def current_head(self, artifact_id: ArtifactId) -> ArtifactRevisionRecord:
        """Return the unique lineage head without relying on presentation order."""
        history = self.history(artifact_id)
        if not history:
            raise LookupError(f"artifact {artifact_id} was not found")
        referenced_priors = {
            item.prior_revision_id for item in history if item.prior_revision_id is not None
        }
        heads = tuple(item for item in history if item.id not in referenced_priors)
        if len(heads) != 1:
            raise ValueError("artifact revision lineage does not have one canonical head")
        return heads[0]

    def pending(self) -> tuple[ArtifactRevisionRecord, ...]:
        return tuple(
            item for item in self.revisions if item.status is ArtifactRevisionStatus.PROPOSED
        )

    def accepted(self, kind: StudyArtifactKind | None = None) -> tuple[ArtifactRevisionRecord, ...]:
        return tuple(
            item
            for item in self.revisions
            if item.status is ArtifactRevisionStatus.ACCEPTED
            and (kind is None or item.kind is kind)
        )

    def children(self, parent_artifact_id: ArtifactId) -> tuple[ArtifactRevisionRecord, ...]:
        return tuple(
            item for item in self.accepted() if item.parent_artifact_id == parent_artifact_id
        )


def _fingerprint(value: str, name: str) -> None:
    require_text(value, name)
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 fingerprint")


__all__ = [
    "ArtifactBatchRecord",
    "ArtifactBulkDecisionReceipt",
    "ArtifactDecisionRecord",
    "ArtifactDecisionRequest",
    "ArtifactDecisionResult",
    "ArtifactProposal",
    "ArtifactProposalOrigin",
    "ArtifactRevisionRecord",
    "ArtifactSnapshot",
    "GeneratedBatchProofReceipt",
    "ServiceDecisionPolicyReceipt",
    "ServiceDecisionPolicyRequest",
    "VerifiedGeneratedArtifactBatch",
    "service_decision_result_fingerprint",
]
