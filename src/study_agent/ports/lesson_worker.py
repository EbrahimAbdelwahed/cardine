"""Inward ports for the lesson-scoped flashcard coordinator."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from study_agent.domain import ExecutionContext, RunId
from study_agent.domain._validation import JsonObject
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerRequest,
    ProfileTaskExpectation,
    ResolvedPlannedBundleEvidence,
    RevisionContentCommitment,
    VerifiedFlashcardPageResult,
)
from study_agent.flashcards.planning import (
    FlashcardLessonPlan,
    PlannedFlashcardBundle,
    PreparedPlannedFlashcardScope,
)
from study_agent.pedagogy import ProfileSelectionReceipt
from study_agent.workers.contracts import GenerationWorkerReceipt, GenerationWorkerTask
from study_agent.workers.view import WorkerCompactView

if TYPE_CHECKING:
    from study_agent.capabilities.worker_adapter import ProfiledWorkerExecutionDescriptor


class PlannedBundleEvidenceResolver(Protocol):
    def resolve(
        self,
        plan: FlashcardLessonPlan,
        bundle: PlannedFlashcardBundle,
        revision_commitments: tuple[RevisionContentCommitment, ...],
        context: ExecutionContext,
    ) -> ResolvedPlannedBundleEvidence: ...


class FlashcardProfileTaskBinding(Protocol):
    @property
    def expectation(self) -> ProfileTaskExpectation: ...

    def build(
        self,
        task_id: str,
        public_inputs: JsonObject,
        prepared_scope: PreparedPlannedFlashcardScope,
        context: ExecutionContext,
    ) -> GenerationWorkerTask: ...


class FlashcardProfileExecutionBinding(FlashcardProfileTaskBinding, Protocol):
    """Profile task binding with the isolated capability execution contract."""

    @property
    def execution_descriptor(self) -> ProfiledWorkerExecutionDescriptor: ...


class PlannedBundleWorker(Protocol):
    async def start(
        self,
        task: GenerationWorkerTask,
        prepared_scope: PreparedPlannedFlashcardScope,
        context: ExecutionContext,
    ) -> WorkerCompactView: ...

    def detail(
        self,
        task_id: str,
        prepared_scope_fingerprint: str,
        context: ExecutionContext,
    ) -> VerifiedFlashcardPageResult: ...


class HistoricalPlannedBundleWorkerRouter(Protocol):
    """Rebuild the exact request-scoped worker selected by persisted history."""

    def for_request(self, request: LessonWorkerRequest) -> PlannedBundleWorker: ...


class LessonWorkerStore(Protocol):
    def create(self, key: str, payload: bytes) -> bool: ...

    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool: ...

    def load(self, key: str) -> bytes: ...


@dataclass(frozen=True, slots=True)
class LessonGeneratedBatchOwnerCommitment:
    lesson_run_id: RunId
    lesson_request_fingerprint: str
    lesson_plan_fingerprint: str
    lesson_profile_fingerprint: str
    coordinator_fingerprint: str
    page_position: int
    bundle_order: tuple[str, ...]
    bundle_id: str
    bundle_fingerprint: str
    wrapper_fingerprint: str
    scope_fingerprint: str
    read_set_fingerprint: str
    revision_commitments_fingerprint: str
    associated_overview_bundle_id: str | None
    overview_association_fingerprint: str | None
    profile_selection_receipt: ProfileSelectionReceipt | None = None


@dataclass(frozen=True, slots=True)
class LessonGeneratedBatchOwnerPublication:
    child_run_id: RunId
    child_task_fingerprint: str
    child_receipt_fingerprint: str
    child_proof_fingerprint: str
    owner_receipt_fingerprint: str


class LessonGeneratedBatchOwnerWriter(Protocol):
    """Load the exact child proof and idempotently persist its lesson owner."""

    def create(
        self,
        commitment: LessonGeneratedBatchOwnerCommitment,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
        context: ExecutionContext,
    ) -> LessonGeneratedBatchOwnerPublication: ...


__all__ = [
    "FlashcardProfileExecutionBinding",
    "FlashcardProfileTaskBinding",
    "HistoricalPlannedBundleWorkerRouter",
    "LessonGeneratedBatchOwnerCommitment",
    "LessonGeneratedBatchOwnerPublication",
    "LessonGeneratedBatchOwnerWriter",
    "LessonWorkerStore",
    "PlannedBundleEvidenceResolver",
    "PlannedBundleWorker",
]
