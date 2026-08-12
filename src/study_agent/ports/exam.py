"""Inward ports for grounded exam-sample analysis."""

from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol

from study_agent.domain import ExecutionContext, RunId
from cardine.exams.contracts import (
    ExamAnalysisRequest,
    ExamEvidenceMapping,
    ExamPromptEvidenceProjection,
    PreparedExamSampleScope,
)
from study_agent.workers import (
    GenerationWorkerReceipt,
    GenerationWorkerTask,
    VerifiedChildExecutionProof,
    VerifiedChildExecutionProofView,
)


class ExamSampleScopePreparationPort(Protocol):
    def prepare(
        self, request: ExamAnalysisRequest, context: ExecutionContext
    ) -> PreparedExamSampleScope: ...


class ExamVerifiedChildProofReader(Protocol):
    def load(
        self,
        task: GenerationWorkerTask,
        run_id: RunId,
        receipt: GenerationWorkerReceipt,
        context: ExecutionContext,
    ) -> VerifiedChildExecutionProofView: ...


@dataclass(frozen=True, slots=True)
class ExamGeneratedBatchOwnerCommitment:
    request: ExamAnalysisRequest
    opaque_request_key_fingerprint: str
    prepared_scope: PreparedExamSampleScope
    prompt_projection: ExamPromptEvidenceProjection
    evidence_mapping: tuple[ExamEvidenceMapping, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_mapping", tuple(self.evidence_mapping))


@dataclass(frozen=True, slots=True)
class ExamGeneratedBatchOwnerPublication:
    child_run_id: RunId
    child_task_fingerprint: str
    child_receipt_fingerprint: str
    child_proof_fingerprint: str
    owner_receipt_fingerprint: str


class ExamGeneratedBatchOwnerWriter(Protocol):
    def create(
        self,
        commitment: ExamGeneratedBatchOwnerCommitment,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
        proof: VerifiedChildExecutionProof,
        context: ExecutionContext,
    ) -> ExamGeneratedBatchOwnerPublication: ...


def exam_opaque_request_key_fingerprint(value: str) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or not value.replace("-", "").isalnum()
    ):
        raise ValueError("opaque request key must be portable")
    return sha256(b"exam-owner-request-key@1\0" + value.encode()).hexdigest()


__all__ = [
    "ExamGeneratedBatchOwnerCommitment",
    "ExamGeneratedBatchOwnerPublication",
    "ExamGeneratedBatchOwnerWriter",
    "ExamSampleScopePreparationPort",
    "ExamVerifiedChildProofReader",
    "exam_opaque_request_key_fingerprint",
]
