"""Convert exact coordinator-owned B1A proofs into canonical artifact proposals."""

from __future__ import annotations

from collections.abc import Mapping
from hashlib import sha256

from cardine.exams.contracts import (
    ExamAnalysisProposal,
    ExamAnalysisRequest,
    ExamEvidenceMapping,
    ExamPromptEvidenceProjection,
)
from study_agent.artifacts.candidates import (
    FlashcardCandidate,
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.domain import (
    ArtifactReadDependency,
    ExecutionContext,
    HybridFlashcardRole,
    ModelProvenance,
    ModelUsageProvenance,
    MorphologyFlashcardRole,
    PromptProvenance,
    RetrievalProvenance,
    RunId,
    SourceCommitment,
    StudyArtifactKind,
    ValidatorProvenance,
    VersionPins,
)
from study_agent.domain._validation import JsonObject, freeze_object
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerCheckpoint,
    LessonWorkerPageStatus,
    authority_fingerprint,
)
from study_agent.flashcards.planning import PreparedPlannedFlashcardScope
from study_agent.ports.exam import (
    ExamGeneratedBatchOwnerCommitment,
    ExamGeneratedBatchOwnerPublication,
    ExamSampleScopePreparationPort,
)
from study_agent.ports.lesson_worker import (
    HistoricalPlannedBundleWorkerRouter,
    LessonGeneratedBatchOwnerCommitment,
    LessonGeneratedBatchOwnerPublication,
    LessonWorkerStore,
)
from study_agent.ports.storage import SourceContentPort
from study_agent.ports.verified_batch import (
    GeneratedBatchOwnerReader,
    GeneratedBatchOwnerResolver,
    VerifiedChildProofReader,
    VerifiedExamOwnerMaterial,
    VerifiedLessonOwnerMaterial,
)
from study_agent.state import canonical_json_bytes
from study_agent.workers import (
    GenerationWorkerReceipt,
    GenerationWorkerStatus,
    GenerationWorkerTask,
    GenerationWorkerTaskKind,
    ObservedValidationReceipt,
    VerifiedChildExecutionProofView,
    generation_worker_child_context,
)

from .content import (
    AnswerBlock,
    EvidenceObservation,
    ExamBlueprintContent,
    HybridFlashcardContent,
    MorphologyFlashcardContent,
    StudyArtifactEnvelope,
)
from .contracts import (
    ArtifactProposal,
    GeneratedBatchProofReceipt,
    VerifiedGeneratedArtifactBatch,
)
from .generated_owner import (
    ExamGeneratedBatchOwnerReceipt,
    GeneratedBatchOwnerReceipt,
    GeneratedBatchOwnerRegistry,
    LessonGeneratedBatchOwnerReceipt,
)
from .identity import GeneratedArtifactProvenance, artifact_provenance_to_bytes

_VERIFIER_ID = "verified-child-artifact-batch"
_VERIFIER_VERSION = "1.0.0"
_BUNDLE_DOMAIN = b"lesson-worker-bundle@1\0"
_EVIDENCE_MAPPING_DOMAIN = b"exam-evidence-mapping@1\0"
_EXAM_COORDINATOR_DOMAIN = b"exam-generated-owner-coordinator@1\0"
_PROOF_DOMAIN = b"verified-generated-artifact-batch@1\0"
_VALIDATOR_GROUP_DOMAIN = b"artifact-validator-receipt-group@1\0"


class VerifiedBatchRecoveryError(ValueError):
    """Owner, coordinator, proof, or canonical conversion commitments differ."""


class UnsupportedVerifiedMediaError(VerifiedBatchRecoveryError):
    """A candidate names media without a persisted verified media receipt."""


class VerifiedLessonOwnerWriterAdapter:
    """Publish one lesson owner only after exact B1A proof recovery."""

    def __init__(
        self,
        proofs: VerifiedChildProofReader,
        owners: GeneratedBatchOwnerRegistry,
    ) -> None:
        self._proofs = proofs
        self._owners = owners

    def create(
        self,
        commitment: LessonGeneratedBatchOwnerCommitment,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
        context: ExecutionContext,
    ) -> LessonGeneratedBatchOwnerPublication:
        if task.task_kind is not GenerationWorkerTaskKind.FLASHCARD_BUNDLE:
            raise VerifiedBatchRecoveryError("lesson owner requires a flashcard task")
        child_context = generation_worker_child_context(task, context)
        proof = self._proofs.load(task, receipt.child_run_id, receipt, child_context)
        _verify_execution(task, receipt, proof)
        owner = LessonGeneratedBatchOwnerReceipt(
            child_run_id=proof.run_id,
            child_task_id=task.task_id,
            child_task_fingerprint=task.fingerprint,
            child_receipt_fingerprint=receipt.fingerprint,
            child_proof_fingerprint=proof.fingerprint,
            lesson_run_id=commitment.lesson_run_id,
            lesson_request_fingerprint=commitment.lesson_request_fingerprint,
            lesson_plan_fingerprint=commitment.lesson_plan_fingerprint,
            lesson_profile_fingerprint=commitment.lesson_profile_fingerprint,
            coordinator_fingerprint=commitment.coordinator_fingerprint,
            page_position=commitment.page_position,
            bundle_order=commitment.bundle_order,
            bundle_id=commitment.bundle_id,
            bundle_fingerprint=commitment.bundle_fingerprint,
            wrapper_fingerprint=commitment.wrapper_fingerprint,
            scope_fingerprint=commitment.scope_fingerprint,
            read_set_fingerprint=commitment.read_set_fingerprint,
            revision_commitments_fingerprint=(commitment.revision_commitments_fingerprint),
            associated_overview_bundle_id=commitment.associated_overview_bundle_id,
            overview_association_fingerprint=(commitment.overview_association_fingerprint),
        )
        stored = self._owners.create(owner)
        return LessonGeneratedBatchOwnerPublication(
            stored.child_run_id,
            stored.child_task_fingerprint,
            stored.child_receipt_fingerprint,
            stored.child_proof_fingerprint,
            stored.fingerprint,
        )


class VerifiedExamOwnerWriterAdapter:
    """Publish one exam owner from the proof already verified by the facade."""

    def __init__(self, owners: GeneratedBatchOwnerRegistry) -> None:
        self._owners = owners

    def create(
        self,
        commitment: ExamGeneratedBatchOwnerCommitment,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
        proof: VerifiedChildExecutionProofView,
        context: ExecutionContext,
    ) -> ExamGeneratedBatchOwnerPublication:
        del context
        if task.task_kind is not GenerationWorkerTaskKind.EXAM_ANALYSIS:
            raise VerifiedBatchRecoveryError("exam owner requires an exam-analysis task")
        _verify_execution(task, receipt, proof)
        commitment.prompt_projection.verify_scope(commitment.prepared_scope)
        if task.payload != commitment.request.to_json():
            raise VerifiedBatchRecoveryError("exam task request changed")
        mapping_fingerprint = _evidence_mapping_fingerprint(commitment.evidence_mapping)
        coordinator_fingerprint = exam_owner_coordinator_fingerprint(
            commitment, task, receipt, proof
        )
        owner = ExamGeneratedBatchOwnerReceipt.create(
            child_run_id=proof.run_id,
            child_task_id=task.task_id,
            child_task_fingerprint=task.fingerprint,
            child_receipt_fingerprint=receipt.fingerprint,
            child_proof_fingerprint=proof.fingerprint,
            task_bytes=task.to_bytes(),
            receipt_bytes=receipt.to_bytes(),
            request_bytes=commitment.request.to_bytes(),
            opaque_request_key_fingerprint=(commitment.opaque_request_key_fingerprint),
            scope_fingerprint=commitment.prepared_scope.scope_fingerprint,
            projection_fingerprint=(commitment.prompt_projection.projection_fingerprint),
            evidence_mapping_fingerprint=mapping_fingerprint,
            coordinator_fingerprint=coordinator_fingerprint,
        )
        stored = self._owners.create(owner)
        return ExamGeneratedBatchOwnerPublication(
            stored.child_run_id,
            stored.child_task_fingerprint,
            stored.child_receipt_fingerprint,
            stored.child_proof_fingerprint,
            stored.fingerprint,
        )


class VerifiedGeneratedOwnerResolverAdapter:
    """Rebuild exact owner material from coordinator stores and trusted sources."""

    def __init__(
        self,
        *,
        lesson_store: LessonWorkerStore,
        lesson_worker: HistoricalPlannedBundleWorkerRouter,
        exam_scope: ExamSampleScopePreparationPort,
        source_content: SourceContentPort,
    ) -> None:
        self._lesson_store = lesson_store
        self._lesson_worker = lesson_worker
        self._exam_scope = exam_scope
        self._source_content = source_content

    def resolve_lesson(
        self,
        owner: LessonGeneratedBatchOwnerReceipt,
        context: ExecutionContext,
    ) -> VerifiedLessonOwnerMaterial:
        checkpoint = LessonWorkerCheckpoint.from_bytes(
            self._lesson_store.load(str(owner.lesson_run_id))
        )
        if not 0 <= owner.page_position < len(checkpoint.pages):
            raise VerifiedBatchRecoveryError("lesson owner page is outside checkpoint")
        page = checkpoint.pages[owner.page_position]
        if page.child_task_bytes is None or page.wrapper_bytes is None:
            raise VerifiedBatchRecoveryError("lesson owner material is incomplete")
        task = GenerationWorkerTask.from_bytes(page.child_task_bytes)
        prepared = PreparedPlannedFlashcardScope.from_bytes(page.wrapper_bytes)
        worker = self._lesson_worker.for_request(checkpoint.request)
        detail = worker.detail(task.task_id, prepared.wrapper_fingerprint, context)
        for item in prepared.prepared_scope.evidence.items:
            resolved = self._source_content.resolve(item.evidence.citation)
            if resolved.citation != item.evidence.citation or resolved.text != item.evidence.text:
                raise VerifiedBatchRecoveryError("lesson source evidence changed")
        return VerifiedLessonOwnerMaterial(checkpoint, task, detail.detail.receipt, prepared)

    def resolve_exam(
        self,
        owner: ExamGeneratedBatchOwnerReceipt,
        context: ExecutionContext,
    ) -> VerifiedExamOwnerMaterial:
        request = ExamAnalysisRequest.from_bytes(owner.request_bytes)
        task = GenerationWorkerTask.from_bytes(owner.task_bytes)
        receipt = GenerationWorkerReceipt.from_bytes(owner.receipt_bytes)
        scope = self._exam_scope.prepare(request, context)
        projection = ExamPromptEvidenceProjection.from_scope(scope)
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
        coordinator = _exam_coordinator_fingerprint(
            request=request,
            opaque_request_key_fingerprint=owner.opaque_request_key_fingerprint,
            task_fingerprint=task.fingerprint,
            receipt_fingerprint=receipt.fingerprint,
            proof_fingerprint=owner.child_proof_fingerprint,
            scope_fingerprint=scope.scope_fingerprint,
            projection_fingerprint=projection.projection_fingerprint,
            evidence_mapping_fingerprint=_evidence_mapping_fingerprint(mappings),
        )
        return VerifiedExamOwnerMaterial(
            request,
            task,
            receipt,
            scope,
            projection,
            mappings,
            owner.opaque_request_key_fingerprint,
            coordinator,
        )


class VerifiedGeneratedBatchAdapter:
    def __init__(
        self,
        *,
        owners: GeneratedBatchOwnerReader,
        resolver: GeneratedBatchOwnerResolver,
        proofs: VerifiedChildProofReader,
    ) -> None:
        self._owners = owners
        self._resolver = resolver
        self._proofs = proofs

    def recover(self, run_id: RunId, context: ExecutionContext) -> VerifiedGeneratedArtifactBatch:
        if not isinstance(run_id, RunId):
            raise TypeError("run_id must be RunId")
        if not isinstance(context, ExecutionContext):
            raise TypeError("context must be ExecutionContext")
        if context.session_id is None:
            raise VerifiedBatchRecoveryError("verified artifact recovery requires a session")
        owner = self._owners.load(run_id)
        if owner.child_run_id != run_id:
            raise VerifiedBatchRecoveryError("owner belongs to another child run")
        if isinstance(owner, LessonGeneratedBatchOwnerReceipt):
            lesson_material = self._resolver.resolve_lesson(owner, context)
            task, receipt = lesson_material.task, lesson_material.receipt
            self._verify_lesson_owner(owner, lesson_material, context)
            self._verify_common_owner(owner, task, receipt)
            child_context = generation_worker_child_context(task, context)
            proof = self._proofs.load(task, run_id, receipt, child_context)
            self._verify_proof(owner, task, receipt, proof)
            proposals = _lesson_proposals(owner, lesson_material, proof)
        elif isinstance(owner, ExamGeneratedBatchOwnerReceipt):
            exam_material = self._resolver.resolve_exam(owner, context)
            task, receipt = exam_material.task, exam_material.receipt
            self._verify_exam_owner(owner, exam_material)
            self._verify_common_owner(owner, task, receipt)
            child_context = generation_worker_child_context(task, context)
            proof = self._proofs.load(task, run_id, receipt, child_context)
            self._verify_proof(owner, task, receipt, proof)
            proposals = _exam_proposals(owner, exam_material, proof)
        else:
            raise TypeError("generated owner kind is unsupported")
        batch_proof = GeneratedBatchProofReceipt(
            _VERIFIER_ID,
            _VERIFIER_VERSION,
            _batch_proof_fingerprint(owner, proof, proposals),
        )
        return VerifiedGeneratedArtifactBatch(
            run_id,
            context.course_id,
            context.session_id,
            proposals,
            batch_proof,
        )

    @staticmethod
    def _verify_common_owner(
        owner: GeneratedBatchOwnerReceipt,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
    ) -> None:
        if (
            task.task_id != owner.child_task_id
            or task.fingerprint != owner.child_task_fingerprint
            or receipt.task_id != task.task_id
            or receipt.task_fingerprint != task.fingerprint
            or receipt.status is not GenerationWorkerStatus.COMPLETED
            or receipt.child_run_id != owner.child_run_id
            or receipt.fingerprint != owner.child_receipt_fingerprint
        ):
            raise VerifiedBatchRecoveryError("owner task or completed receipt changed")

    @staticmethod
    def _verify_lesson_owner(
        owner: LessonGeneratedBatchOwnerReceipt,
        material: VerifiedLessonOwnerMaterial,
        context: ExecutionContext,
    ) -> None:
        checkpoint = material.checkpoint
        request = checkpoint.request
        expected_authority = authority_fingerprint(
            freeze_object(
                {
                    "principal_kind": context.principal_kind.value,
                    "principal_id": context.principal_id,
                    "course_id": str(context.course_id),
                    "session_id": str(context.session_id) if context.session_id else None,
                    "required_authority": request.profile_expectation.required_authority,
                }
            )
        )
        if (
            checkpoint.run_id != owner.lesson_run_id
            or checkpoint.authority_fingerprint != expected_authority
            or checkpoint.fingerprint != owner.coordinator_fingerprint
            or checkpoint.request_fingerprint != owner.lesson_request_fingerprint
            or request.plan.plan_fingerprint != owner.lesson_plan_fingerprint
            or request.profile_expectation.profile_fingerprint != owner.lesson_profile_fingerprint
            or tuple(item.bundle_id for item in request.plan.bundles) != owner.bundle_order
            or owner.page_position >= len(checkpoint.pages)
        ):
            raise VerifiedBatchRecoveryError("lesson coordinator ownership changed")
        page = checkpoint.pages[owner.page_position]
        bundle = request.plan.bundles[owner.page_position]
        prepared = material.prepared_scope
        expected_bundle_fingerprint = sha256(
            _BUNDLE_DOMAIN + canonical_json_bytes(bundle.to_json())
        ).hexdigest()
        if (
            page.status is not LessonWorkerPageStatus.CHILD_TERMINAL
            or page.receipt is None
            or page.receipt.failure_code is not None
            or page.child_task_bytes != material.task.to_bytes()
            or page.receipt.child_run_id != owner.child_run_id
            or page.receipt.child_receipt_fingerprint != owner.child_receipt_fingerprint
            or page.bundle_id != owner.bundle_id
            or bundle.bundle_id != owner.bundle_id
            or expected_bundle_fingerprint != owner.bundle_fingerprint
            or prepared.bundle_id != owner.bundle_id
            or prepared.plan_fingerprint != owner.lesson_plan_fingerprint
            or prepared.wrapper_fingerprint != owner.wrapper_fingerprint
            or prepared.prepared_scope.scope_fingerprint != owner.scope_fingerprint
            or prepared.prepared_scope.evidence.read_set_fingerprint != owner.read_set_fingerprint
            or request.revision_commitments_fingerprint != owner.revision_commitments_fingerprint
        ):
            raise VerifiedBatchRecoveryError("lesson page ownership changed")

    @staticmethod
    def _verify_exam_owner(
        owner: ExamGeneratedBatchOwnerReceipt,
        material: VerifiedExamOwnerMaterial,
    ) -> None:
        scope = material.prepared_scope
        projection = material.prompt_projection
        projection.verify_scope(scope)
        if (
            material.request.to_bytes() != owner.request_bytes
            or material.task.task_kind is not GenerationWorkerTaskKind.EXAM_ANALYSIS
            or material.opaque_request_key_fingerprint != owner.opaque_request_key_fingerprint
            or material.coordinator_fingerprint != owner.coordinator_fingerprint
            or scope.scope_fingerprint != owner.scope_fingerprint
            or projection.projection_fingerprint != owner.projection_fingerprint
            or _evidence_mapping_fingerprint(material.evidence_mapping)
            != owner.evidence_mapping_fingerprint
        ):
            raise VerifiedBatchRecoveryError("exam coordinator ownership changed")

    @staticmethod
    def _verify_proof(
        owner: GeneratedBatchOwnerReceipt,
        task: GenerationWorkerTask,
        receipt: GenerationWorkerReceipt,
        proof: VerifiedChildExecutionProofView,
    ) -> None:
        if (
            proof.run_id != owner.child_run_id
            or proof.fingerprint != owner.child_proof_fingerprint
            or proof.input_fingerprint != task.payload_fingerprint
            or proof.output_fingerprint != receipt.output_fingerprint
            or proof.definition_fingerprint != task.definition_fingerprint
            or proof.pins != task.pins
        ):
            raise VerifiedBatchRecoveryError("verified child proof changed")


def _lesson_proposals(
    owner: LessonGeneratedBatchOwnerReceipt,
    material: VerifiedLessonOwnerMaterial,
    proof: VerifiedChildExecutionProofView,
) -> tuple[ArtifactProposal, ...]:
    if material.task.task_kind is not GenerationWorkerTaskKind.FLASHCARD_BUNDLE:
        raise VerifiedBatchRecoveryError("lesson owner resolved a non-flashcard task")
    if not isinstance(proof.output, Mapping):
        raise VerifiedBatchRecoveryError("flashcard proof output must be an object")
    batch = FlashcardCandidateBatch.from_json(proof.output)
    if not batch.candidates:
        raise VerifiedBatchRecoveryError("verified page has no artifact candidates")
    prepared = material.prepared_scope
    evidence = prepared.prepared_scope.evidence
    selection = material.checkpoint.request.profile_expectation.profile_selection_receipt
    key_to_ordinal = {
        candidate.candidate_key: ordinal for ordinal, candidate in enumerate(batch.candidates)
    }
    proposals: list[ArtifactProposal] = []
    for ordinal, candidate in enumerate(batch.candidates):
        if candidate.media_evidence_ids:
            raise UnsupportedVerifiedMediaError(
                "verified media receipt is not persisted in the child proof"
            )
        commitments = _candidate_commitments(candidate, prepared)
        indices = tuple(range(len(commitments)))
        blocks = tuple(
            AnswerBlock(item.label, item.text, item.key_points) for item in candidate.answer_blocks
        )
        parent = (
            key_to_ordinal[candidate.parent_candidate_key]
            if candidate.parent_candidate_key is not None
            else None
        )
        if candidate.pedagogical_role in {
            FlashcardPedagogicalRole.OVERVIEW,
            FlashcardPedagogicalRole.SECTION,
            FlashcardPedagogicalRole.DETAIL,
        }:
            content: HybridFlashcardContent | MorphologyFlashcardContent = HybridFlashcardContent(
                candidate.retrieval_form,
                candidate.prompt,
                blocks,
                HybridFlashcardRole(candidate.pedagogical_role.value),
                candidate.rationale,
                indices,
                parent,
            )
        else:
            if candidate.morphology_family is None or candidate.cognitive_function is None:
                raise VerifiedBatchRecoveryError("morphology candidate fields are incomplete")
            content = MorphologyFlashcardContent(
                candidate.retrieval_form,
                candidate.prompt,
                blocks,
                MorphologyFlashcardRole(candidate.pedagogical_role.value),
                candidate.morphology_family,
                candidate.cognitive_function,
                candidate.rationale,
                indices,
                parent,
            )
        envelope = StudyArtifactEnvelope(StudyArtifactKind.FLASHCARD, content)
        proposals.append(
            ArtifactProposal(
                ordinal,
                envelope,
                _provenance(proof, commitments, evidence, selection, envelope),
            )
        )
    return tuple(proposals)


def _exam_proposals(
    owner: ExamGeneratedBatchOwnerReceipt,
    material: VerifiedExamOwnerMaterial,
    proof: VerifiedChildExecutionProofView,
) -> tuple[ArtifactProposal, ...]:
    if not isinstance(proof.output, Mapping):
        raise VerifiedBatchRecoveryError("exam proof output must be an object")
    proposal = ExamAnalysisProposal.from_json(proof.output)
    mappings = {item.evidence_id: item for item in material.evidence_mapping}
    handles = tuple(
        dict.fromkeys(
            evidence_id
            for observation in (*proposal.observed_topics, *proposal.observed_formats)
            for evidence_id in observation.evidence_ids
        )
    )
    if not handles or any(handle not in mappings for handle in handles):
        raise VerifiedBatchRecoveryError("exam observations cite an unverified mapping")
    commitments = tuple(_mapping_commitment(mappings[handle]) for handle in handles)
    index = {handle: position for position, handle in enumerate(handles)}
    topics = tuple(
        EvidenceObservation(item.value, tuple(index[key] for key in item.evidence_ids))
        for item in proposal.observed_topics
    )
    formats = tuple(
        EvidenceObservation(item.value, tuple(index[key] for key in item.evidence_ids))
        for item in proposal.observed_formats
    )
    content = StudyArtifactEnvelope(
        StudyArtifactKind.EXAM_BLUEPRINT,
        ExamBlueprintContent(proposal.sample_size, topics, formats, proposal.limitations),
    )
    return (
        ArtifactProposal(
            0,
            content,
            _provenance(
                proof,
                commitments,
                material.prepared_scope.evidence,
                None,
                content,
            ),
        ),
    )


def _candidate_commitments(
    candidate: FlashcardCandidate,
    prepared: PreparedPlannedFlashcardScope,
) -> tuple[SourceCommitment, ...]:
    trusted = prepared.prepared_scope.evidence.by_handle()
    if any(handle not in trusted for handle in candidate.evidence_ids):
        raise VerifiedBatchRecoveryError("candidate cites evidence outside verified scope")
    return tuple(
        SourceCommitment(
            trusted[handle].citation.source_id,
            trusted[handle].citation.revision_id,
            trusted[handle].citation.chunk_id,
            trusted[handle].citation.start_offset,
            trusted[handle].citation.end_offset,
        )
        for handle in candidate.evidence_ids
    )


def _mapping_commitment(value: ExamEvidenceMapping) -> SourceCommitment:
    return SourceCommitment(
        value.source_id,
        value.revision_id,
        value.chunk_id,
        value.start_offset,
        value.end_offset,
    )


def _provenance(
    proof: VerifiedChildExecutionProofView,
    commitments: tuple[SourceCommitment, ...],
    evidence: object,
    profile_selection: object,
    content: StudyArtifactEnvelope,
) -> GeneratedArtifactProvenance:
    from study_agent.grounding import EvidenceEnvelope
    from study_agent.pedagogy import ProfileSelectionReceipt

    if not isinstance(evidence, EvidenceEnvelope):
        raise TypeError("verified evidence envelope is invalid")
    if profile_selection is not None and not isinstance(profile_selection, ProfileSelectionReceipt):
        raise TypeError("profile selection receipt is invalid")
    usage = (
        ModelUsageProvenance(proof.model.input_tokens, proof.model.output_tokens)
        if proof.model.input_tokens is not None and proof.model.output_tokens is not None
        else None
    )
    model = ModelProvenance(
        proof.model.adapter_id,
        proof.model.adapter_version,
        proof.model.model_id,
        proof.model.response_id,
        proof.run_id,
        usage,
    )
    pins = proof.pins
    domain_pins = VersionPins(
        f"{pins.skill.id}@{pins.skill.version}",
        f"{pins.playbook.id}@{pins.playbook.version}",
        f"{pins.prompt.id}@{pins.prompt.version}",
        f"{pins.model_adapter.id}@{pins.model_adapter.version}",
        f"{pins.state_contract.id}@{pins.state_contract.version}",
        ",".join(f"{item.tool_name}@{item.version}" for item in pins.tool_behaviors),
    )
    dependencies = tuple(
        ArtifactReadDependency(item.kind, item.id, item.version) for item in proof.read_dependencies
    )
    return GeneratedArtifactProvenance(
        commitments,
        PromptProvenance(
            proof.prompt.prompt_id,
            proof.prompt.prompt_version,
            proof.prompt.composition_fingerprint,
            proof.prompt.layer_fingerprints,
        ),
        model,
        RetrievalProvenance(
            evidence.strategy_id,
            evidence.strategy_version,
            evidence.query_fingerprint,
            evidence.index_version,
            evidence.read_set_fingerprint,
        ),
        _validator_provenance(proof),
        domain_pins,
        profile_selection,
        dependencies,
        sha256(content.to_bytes()).hexdigest(),
        proof.run_id,
    )


def _validator_provenance(
    proof: VerifiedChildExecutionProofView,
) -> tuple[ValidatorProvenance, ...]:
    grouped: dict[tuple[str, str], list[ObservedValidationReceipt]] = {}
    for item in proof.validations:
        grouped.setdefault((item.validator_id, item.validator_version), []).append(item)
    result: list[ValidatorProvenance] = []
    for (validator_id, version), raw_items in grouped.items():
        items = tuple(raw_items)
        fingerprint = (
            items[0].result_fingerprint
            if len(items) == 1
            else sha256(
                _VALIDATOR_GROUP_DOMAIN
                + canonical_json_bytes({"receipts": tuple(item.to_json() for item in items)})
            ).hexdigest()
        )
        result.append(
            ValidatorProvenance(
                validator_id,
                version,
                all(item.passed for item in items),
                items[-1].disposition.value,
                fingerprint,
            )
        )
    return tuple(result)


def exam_owner_coordinator_fingerprint(
    commitment: ExamGeneratedBatchOwnerCommitment,
    task: GenerationWorkerTask,
    receipt: GenerationWorkerReceipt,
    proof: VerifiedChildExecutionProofView,
) -> str:
    return _exam_coordinator_fingerprint(
        request=commitment.request,
        opaque_request_key_fingerprint=commitment.opaque_request_key_fingerprint,
        task_fingerprint=task.fingerprint,
        receipt_fingerprint=receipt.fingerprint,
        proof_fingerprint=proof.fingerprint,
        scope_fingerprint=commitment.prepared_scope.scope_fingerprint,
        projection_fingerprint=commitment.prompt_projection.projection_fingerprint,
        evidence_mapping_fingerprint=_evidence_mapping_fingerprint(commitment.evidence_mapping),
    )


def _exam_coordinator_fingerprint(
    *,
    request: ExamAnalysisRequest,
    opaque_request_key_fingerprint: str,
    task_fingerprint: str,
    receipt_fingerprint: str,
    proof_fingerprint: str,
    scope_fingerprint: str,
    projection_fingerprint: str,
    evidence_mapping_fingerprint: str,
) -> str:
    return sha256(
        _EXAM_COORDINATOR_DOMAIN
        + canonical_json_bytes(
            {
                "request": request.to_json(),
                "opaque_request_key_fingerprint": opaque_request_key_fingerprint,
                "task_fingerprint": task_fingerprint,
                "receipt_fingerprint": receipt_fingerprint,
                "proof_fingerprint": proof_fingerprint,
                "scope_fingerprint": scope_fingerprint,
                "projection_fingerprint": projection_fingerprint,
                "evidence_mapping_fingerprint": evidence_mapping_fingerprint,
            }
        )
    ).hexdigest()


def _verify_execution(
    task: GenerationWorkerTask,
    receipt: GenerationWorkerReceipt,
    proof: VerifiedChildExecutionProofView,
) -> None:
    if (
        receipt.status is not GenerationWorkerStatus.COMPLETED
        or receipt.task_id != task.task_id
        or receipt.task_fingerprint != task.fingerprint
        or receipt.child_run_id != proof.run_id
        or receipt.input_fingerprint != task.payload_fingerprint
        or receipt.output_fingerprint != proof.output_fingerprint
        or proof.input_fingerprint != task.payload_fingerprint
        or proof.definition_fingerprint != task.definition_fingerprint
        or proof.pins != task.pins
    ):
        raise VerifiedBatchRecoveryError("task, receipt, and child proof changed")


def _evidence_mapping_fingerprint(
    mappings: tuple[ExamEvidenceMapping, ...],
) -> str:
    value: JsonObject = freeze_object(
        {
            "mappings": tuple(
                {
                    "evidence_id": item.evidence_id,
                    "sample_key": item.sample_key,
                    "source_id": str(item.source_id),
                    "revision_id": str(item.revision_id),
                    "chunk_id": str(item.chunk_id),
                    "start_offset": item.start_offset,
                    "end_offset": item.end_offset,
                }
                for item in mappings
            )
        }
    )
    return sha256(_EVIDENCE_MAPPING_DOMAIN + canonical_json_bytes(value)).hexdigest()


def _batch_proof_fingerprint(
    owner: GeneratedBatchOwnerReceipt,
    proof: VerifiedChildExecutionProofView,
    proposals: tuple[ArtifactProposal, ...],
) -> str:
    return sha256(
        _PROOF_DOMAIN
        + canonical_json_bytes(
            {
                "owner_fingerprint": owner.fingerprint,
                "child_proof_fingerprint": proof.fingerprint,
                "proposals": tuple(
                    {
                        "ordinal": item.ordinal,
                        "content": item.content.to_bytes().decode("utf-8"),
                        "provenance": artifact_provenance_to_bytes(item.provenance).decode("utf-8"),
                    }
                    for item in proposals
                ),
            }
        )
    ).hexdigest()


__all__ = [
    "UnsupportedVerifiedMediaError",
    "VerifiedBatchRecoveryError",
    "VerifiedExamOwnerWriterAdapter",
    "VerifiedGeneratedBatchAdapter",
    "VerifiedGeneratedOwnerResolverAdapter",
    "VerifiedLessonOwnerWriterAdapter",
    "exam_owner_coordinator_fingerprint",
]
