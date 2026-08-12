"""Inward resolution seams for verified generated artifact recovery."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from study_agent.domain import ExecutionContext, RunId

if TYPE_CHECKING:
    from cardine.exams.contracts import (
        ExamAnalysisRequest,
        ExamEvidenceMapping,
        ExamPromptEvidenceProjection,
        PreparedExamSampleScope,
    )
    from study_agent.artifacts.generated_owner import (
        ExamGeneratedBatchOwnerReceipt,
        GeneratedBatchOwnerReceipt,
        LessonGeneratedBatchOwnerReceipt,
    )
    from study_agent.domain._validation import JsonObject
    from study_agent.flashcards.lesson_worker_contracts import LessonWorkerCheckpoint
    from study_agent.flashcards.planning import PreparedPlannedFlashcardScope
    from study_agent.workers import (
        GenerationWorkerReceipt,
        GenerationWorkerTask,
        VerifiedChildExecutionProofView,
    )


@dataclass(frozen=True, slots=True)
class VerifiedLessonOwnerMaterial:
    checkpoint: LessonWorkerCheckpoint
    task: GenerationWorkerTask
    receipt: GenerationWorkerReceipt
    prepared_scope: PreparedPlannedFlashcardScope


@dataclass(frozen=True, slots=True)
class VerifiedExamOwnerMaterial:
    request: ExamAnalysisRequest
    task: GenerationWorkerTask
    receipt: GenerationWorkerReceipt
    prepared_scope: PreparedExamSampleScope
    prompt_projection: ExamPromptEvidenceProjection
    evidence_mapping: tuple[ExamEvidenceMapping, ...]
    opaque_request_key_fingerprint: str
    coordinator_fingerprint: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "evidence_mapping", tuple(self.evidence_mapping))


class GeneratedBatchOwnerReader(Protocol):
    def load(self, child_run_id: RunId) -> GeneratedBatchOwnerReceipt: ...


class GeneratedBatchOwnerResolver(Protocol):
    def resolve_lesson(
        self,
        owner: LessonGeneratedBatchOwnerReceipt,
        context: ExecutionContext,
    ) -> VerifiedLessonOwnerMaterial: ...

    def resolve_exam(
        self,
        owner: ExamGeneratedBatchOwnerReceipt,
        context: ExecutionContext,
    ) -> VerifiedExamOwnerMaterial: ...


class VerifiedChildProofReader(Protocol):
    def load(
        self,
        task: GenerationWorkerTask,
        run_id: RunId,
        receipt: GenerationWorkerReceipt,
        context: ExecutionContext,
        execution_inputs: JsonObject | None = None,
    ) -> VerifiedChildExecutionProofView: ...


__all__ = [
    "GeneratedBatchOwnerReader",
    "GeneratedBatchOwnerResolver",
    "VerifiedChildProofReader",
    "VerifiedExamOwnerMaterial",
    "VerifiedLessonOwnerMaterial",
]
