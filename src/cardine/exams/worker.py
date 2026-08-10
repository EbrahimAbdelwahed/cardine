"""Thin B1 facade and views for verified exam analysis."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from study_agent.domain import ExecutionContext, RunId
from study_agent.exams.analysis import ExamAnalysisTaskFactory
from study_agent.exams.contracts import (
    ExamAnalysisProofReference,
    ExamAnalysisProposal,
    ExamAnalysisRequest,
    ExamEvidenceMapping,
    ExamPromptEvidenceProjection,
    PreparedExamSampleScope,
)
from study_agent.ports.exam import (
    ExamGeneratedBatchOwnerCommitment,
    ExamGeneratedBatchOwnerPublication,
    ExamGeneratedBatchOwnerWriter,
    ExamVerifiedChildProofReader,
    exam_opaque_request_key_fingerprint,
)
from study_agent.workers import (
    GenerationWorkerService,
    GenerationWorkerStatus,
    generation_worker_child_context,
)


@dataclass(frozen=True, slots=True)
class ExamAnalysisCompactView:
    task_id: str
    run_id: RunId | None
    status: GenerationWorkerStatus
    sample_size: int
    topic_count: int
    format_count: int
    evidence_coverage_count: int
    limitation_codes: tuple[str, ...]
    detail_available: bool


@dataclass(frozen=True, slots=True)
class ExamAnalysisDetailView:
    proposal: ExamAnalysisProposal
    evidence_mapping: tuple[ExamEvidenceMapping, ...]
    proof_reference: ExamAnalysisProofReference
    owner_publication: ExamGeneratedBatchOwnerPublication


class ExamAnalysisFacade:
    def __init__(
        self,
        factory: ExamAnalysisTaskFactory,
        worker: GenerationWorkerService,
        proof_reader: ExamVerifiedChildProofReader,
        owner_writer: ExamGeneratedBatchOwnerWriter,
    ) -> None:
        self._factory = factory
        self._worker = worker
        self._proof_reader = proof_reader
        self._owner_writer = owner_writer

    async def start(
        self,
        request: ExamAnalysisRequest,
        opaque_request_key: str,
        parent: ExecutionContext,
    ) -> ExamAnalysisCompactView:
        task = self._factory.build(request, opaque_request_key)
        view = await self._worker.start(task, parent)
        if view.status is GenerationWorkerStatus.COMPLETED:
            detail = self.detail(request, opaque_request_key, parent)
            cited = {
                evidence_id
                for observation in (
                    *detail.proposal.observed_topics,
                    *detail.proposal.observed_formats,
                )
                for evidence_id in observation.evidence_ids
            }
            return ExamAnalysisCompactView(
                task.task_id,
                view.child_run_id,
                view.status,
                detail.proposal.sample_size,
                len(detail.proposal.observed_topics),
                len(detail.proposal.observed_formats),
                len(cited),
                detail.proposal.limitations,
                True,
            )
        return ExamAnalysisCompactView(
            task.task_id,
            view.child_run_id,
            view.status,
            len(request.sample_revision_ids),
            0,
            0,
            0,
            (),
            view.verified_detail_available,
        )

    def detail(
        self,
        request: ExamAnalysisRequest,
        opaque_request_key: str,
        parent: ExecutionContext,
    ) -> ExamAnalysisDetailView:
        task = self._factory.build(request, opaque_request_key)
        detail = self._worker.detail(task.task_id, parent)
        if not isinstance(detail.output, Mapping):
            raise ValueError("verified exam output must be an object")
        proposal = ExamAnalysisProposal.from_json(detail.output)
        child_context = generation_worker_child_context(task, parent)
        proof = self._proof_reader.load(
            task,
            detail.receipt.child_run_id,
            detail.receipt,
            child_context,
        )
        outputs = tuple(
            item
            for item in proof.tool_outputs
            if item.step_id == "prepare_exam_sample_scope"
            and item.output_key == "prepared_exam"
            and item.tool_id == "source.prepare_exam_sample_scope"
            and item.tool_version == "1.0.0"
        )
        if len(outputs) != 1 or not isinstance(outputs[0].value, Mapping):
            raise ValueError("verified exam preparation proof is missing")
        value = outputs[0].value
        if set(value) != {"prepared_scope", "prompt_projection"}:
            raise ValueError("verified exam preparation output fields changed")
        scope_value = value["prepared_scope"]
        projection_value = value["prompt_projection"]
        if not isinstance(scope_value, Mapping) or not isinstance(projection_value, Mapping):
            raise ValueError("verified exam preparation members are invalid")
        scope = PreparedExamSampleScope.from_json(scope_value)
        projection = ExamPromptEvidenceProjection.from_json(projection_value)
        projection.verify_scope(scope)
        by_handle = {item.handle: item.evidence for item in scope.evidence.items}
        mappings = tuple(
            ExamEvidenceMapping(
                handle,
                sample.sample_key,
                sample.source_id,
                sample.revision_id,
                by_handle[handle].chunk.chunk_id,
                by_handle[handle].citation.start_offset,
                by_handle[handle].citation.end_offset,
            )
            for sample in scope.samples
            for handle in sample.evidence_ids
        )
        publication = self._owner_writer.create(
            ExamGeneratedBatchOwnerCommitment(
                request,
                exam_opaque_request_key_fingerprint(opaque_request_key),
                scope,
                projection,
                mappings,
            ),
            task,
            detail.receipt,
            proof,
            parent,
        )
        if (
            publication.child_run_id != proof.run_id
            or publication.child_task_fingerprint != task.fingerprint
            or publication.child_receipt_fingerprint != detail.receipt.fingerprint
            or publication.child_proof_fingerprint != proof.fingerprint
        ):
            raise ValueError("exam owner publication changed verified child identity")
        return ExamAnalysisDetailView(
            proposal,
            mappings,
            ExamAnalysisProofReference(
                task.task_id,
                proof.run_id,
                detail.receipt.fingerprint,
                proof.fingerprint,
            ),
            publication,
        )


__all__ = ["ExamAnalysisCompactView", "ExamAnalysisDetailView", "ExamAnalysisFacade"]
