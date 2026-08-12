from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from typing import cast

import pytest

from study_agent.artifacts.candidates import (
    FlashcardAnswerBlock,
    FlashcardCandidate,
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.artifacts.content import ExamBlueprintContent, HybridFlashcardContent
from study_agent.artifacts.generated_owner import (
    ExamGeneratedBatchOwnerReceipt,
    GeneratedBatchOwnerReceipt,
    GeneratedBatchOwnerRegistry,
    LessonGeneratedBatchOwnerReceipt,
)
from study_agent.artifacts.identity import GeneratedArtifactProvenance
from study_agent.artifacts.verified_batch import (
    UnsupportedVerifiedMediaError,
    VerifiedBatchRecoveryError,
    VerifiedExamOwnerWriterAdapter,
    VerifiedGeneratedBatchAdapter,
    VerifiedGeneratedOwnerResolverAdapter,
    VerifiedLessonOwnerWriterAdapter,
    exam_owner_coordinator_fingerprint,
)
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    ResolvedCitation,
    RetrievalForm,
    RevisionId,
    RunId,
    SessionId,
)
from study_agent.domain._validation import JsonObject, freeze_object
from cardine.exams.analysis import ExamAnalysisTaskFactory, analyze_exam_sample_binding
from cardine.exams.contracts import (
    ExamAnalysisRequest,
    ExamEvidenceMapping,
    ExamPromptEvidenceProjection,
)
from cardine.exams.worker import ExamAnalysisFacade
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerCheckpoint,
    LessonWorkerPageCheckpoint,
    LessonWorkerPageReceipt,
    LessonWorkerPageStatus,
    ResolvedPlannedBundleEvidence,
    VerifiedFlashcardPageResult,
    authority_fingerprint,
    child_task_id,
    lesson_run_id,
)
from study_agent.playbooks import ReadDependency, ValidatorDisposition
from study_agent.ports.exam import (
    ExamGeneratedBatchOwnerCommitment,
    exam_opaque_request_key_fingerprint,
)
from study_agent.ports.lesson_worker import LessonGeneratedBatchOwnerCommitment
from study_agent.ports.verified_batch import (
    VerifiedExamOwnerMaterial,
    VerifiedLessonOwnerMaterial,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.state import canonical_json_bytes
from study_agent.workers import (
    GenerationWorkerReceipt,
    GenerationWorkerService,
    GenerationWorkerStatus,
    GenerationWorkerTask,
    ObservedValidationReceipt,
    TechnicalModelReceipt,
    VerifiedChildExecutionProof,
    VerifiedPromptReceipt,
    VerifiedToolOutput,
    generation_worker_child_context,
)
from study_agent.workers.contracts import fingerprint_output
from study_agent.workers.proof import verified_child_value_fingerprint
from study_agent.workers.view import WorkerDetailView
from tests.unit.exams.test_exam_analysis import _scope as _exam_scope
from tests.unit.flashcards.test_lesson_worker_contracts import _request, _wrapper
from tests.unit.flashcards.test_lesson_worker_service import _Binding

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
SHA_D = "d" * 64


def _context(*, authority: str = "source.read") -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "tutor-service",
        CourseId("course-1"),
        CorrelationId("artifact-recovery"),
        frozenset({authority}),
        SessionId("session-1"),
        idempotency_key="artifact-retry",
    )


def _worker_proof(
    task: GenerationWorkerTask,
    output: JsonObject,
    dependencies: tuple[ReadDependency, ...],
) -> tuple[VerifiedChildExecutionProof, GenerationWorkerReceipt]:
    validations = tuple(
        ObservedValidationReceipt(
            item.step_id,
            item.source,
            item.validator_id,
            item.validator_version,
            True,
            sha256(f"validation-{index}".encode()).hexdigest(),
            ValidatorDisposition.CONTINUE,
        )
        for index, item in enumerate(task.expected_validations)
    )
    proof = VerifiedChildExecutionProof(
        RunId(f"run-{task.task_id[-16:]}"),
        GenerationWorkerStatus.COMPLETED,
        task.definition_fingerprint,
        task.pins,
        task.payload_fingerprint,
        output,
        fingerprint_output(output),
        dependencies,
        (),
        TechnicalModelReceipt(
            task.pins.model_adapter.id,
            str(task.pins.model_adapter.version),
            "fixture-model",
            None,
            12,
            7,
        ),
        VerifiedPromptReceipt(
            task.pins.prompt.id,
            str(task.pins.prompt.version),
            SHA_A,
        ),
        validations,
    )
    receipt = GenerationWorkerReceipt(
        task.task_id,
        task.task_kind,
        GenerationWorkerStatus.COMPLETED,
        proof.run_id,
        task.fingerprint,
        task.pins_fingerprint,
        task.payload_fingerprint,
        proof.output_fingerprint,
        SHA_B,
        SHA_C,
        SHA_A,
    )
    return proof, receipt


def _flashcard_fixture(
    *, media: bool = False
) -> tuple[
    LessonGeneratedBatchOwnerReceipt,
    VerifiedLessonOwnerMaterial,
    VerifiedChildExecutionProof,
    ExecutionContext,
]:
    context = _context()
    request = _request()
    wrapper = _wrapper(request)
    authority = authority_fingerprint(
        freeze_object(
            {
                "principal_kind": context.principal_kind.value,
                "principal_id": context.principal_id,
                "course_id": str(context.course_id),
                "session_id": str(context.session_id),
                "required_authority": request.profile_expectation.required_authority,
            }
        )
    )
    lesson_id = lesson_run_id(request, authority)
    bundle = request.plan.bundles[0]
    task_id = child_task_id(lesson_id, request, bundle, wrapper)
    task = _Binding(request).build(task_id, request.to_public_inputs(), wrapper, context)
    handle = wrapper.prepared_scope.evidence.items[0].handle
    batch = FlashcardCandidateBatch(
        (
            FlashcardCandidate(
                "section-1",
                None,
                RetrievalForm.DIRECT_RECALL,
                "What is the framework?",
                (FlashcardAnswerBlock("Answer", "Framework", ("Framework",)),),
                FlashcardPedagogicalRole.SECTION,
                None,
                None,
                "Establish the section.",
                (handle,),
                ("media-1",) if media else (),
            ),
            FlashcardCandidate(
                "detail-1",
                "section-1",
                RetrievalForm.DIRECT_RECALL,
                "Which detail follows?",
                (FlashcardAnswerBlock("Answer", "Detail", ("Detail",)),),
                FlashcardPedagogicalRole.DETAIL,
                None,
                None,
                "Test one detail.",
                (handle,),
                (),
            ),
        ),
        (),
    )
    citation = wrapper.prepared_scope.evidence.items[0].evidence.citation
    proof, receipt = _worker_proof(
        task,
        batch.to_json(),
        (ReadDependency("source_revision", str(citation.source_id), str(citation.revision_id)),),
    )
    resolution = ResolvedPlannedBundleEvidence(
        wrapper.prepared_scope.evidence,
        request.revision_commitments,
        request.plan.plan_fingerprint,
        bundle.bundle_id,
    )
    page_receipt = LessonWorkerPageReceipt(
        0,
        bundle.bundle_id,
        task.task_id,
        proof.run_id,
        receipt.fingerprint,
        2,
        0,
        None,
    )
    pages = [
        LessonWorkerPageCheckpoint(
            item.relative_position,
            item.bundle_id,
            LessonWorkerPageStatus.PENDING,
            child_task_id(lesson_id, request, item, None),
        )
        for item in request.plan.bundles
    ]
    pages[0] = LessonWorkerPageCheckpoint(
        0,
        bundle.bundle_id,
        LessonWorkerPageStatus.CHILD_TERMINAL,
        task.task_id,
        resolution.fingerprint,
        wrapper.to_bytes(),
        task.to_bytes(),
        page_receipt,
    )
    checkpoint = LessonWorkerCheckpoint(
        request.to_bytes(), request.fingerprint, authority, lesson_id, tuple(pages)
    )
    bundle_fingerprint = sha256(
        b"lesson-worker-bundle@1\0" + canonical_json_bytes(bundle.to_json())
    ).hexdigest()
    owner = LessonGeneratedBatchOwnerReceipt(
        proof.run_id,
        task.task_id,
        task.fingerprint,
        receipt.fingerprint,
        proof.fingerprint,
        lesson_id,
        request.fingerprint,
        request.plan.plan_fingerprint,
        request.profile_expectation.profile_fingerprint,
        checkpoint.fingerprint,
        0,
        tuple(item.bundle_id for item in request.plan.bundles),
        bundle.bundle_id,
        bundle_fingerprint,
        wrapper.wrapper_fingerprint,
        wrapper.prepared_scope.scope_fingerprint,
        wrapper.prepared_scope.evidence.read_set_fingerprint,
        request.revision_commitments_fingerprint,
    )
    return owner, VerifiedLessonOwnerMaterial(checkpoint, task, receipt, wrapper), proof, context


def _mapping_fingerprint(mappings: tuple[ExamEvidenceMapping, ...]) -> str:
    return sha256(
        b"exam-evidence-mapping@1\0"
        + canonical_json_bytes(
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
    ).hexdigest()


def _exam_fixture() -> tuple[
    ExamGeneratedBatchOwnerReceipt,
    VerifiedExamOwnerMaterial,
    VerifiedChildExecutionProof,
    ExecutionContext,
]:
    context = _context(authority="course:read")
    version = SemanticVersion.parse("1.0.0")
    binding = analyze_exam_sample_binding(
        dependency_resolver=lambda *, context, inputs: (),
        model_adapter=ArtifactReference("model-adapter", version),
        state_contract=ArtifactReference("event-state", version),
    )
    request = ExamAnalysisRequest((RevisionId("exam-revision"),), "it")
    task = ExamAnalysisTaskFactory(binding).build(request, "opaque-key-1")
    scope = _exam_scope()
    projection = ExamPromptEvidenceProjection.from_scope(scope)
    evidence = scope.evidence.items[0]
    citation = evidence.evidence.citation
    mapping = (
        ExamEvidenceMapping(
            evidence.handle,
            "sample-1",
            citation.source_id,
            citation.revision_id,
            citation.chunk_id,
            citation.start_offset,
            citation.end_offset,
        ),
    )
    output: JsonObject = {
        "sample_size": 1,
        "observed_topics": ({"value": "Brachial plexus", "evidence_ids": (evidence.handle,)},),
        "observed_formats": ({"value": "Open response", "evidence_ids": (evidence.handle,)},),
        "limitations": (
            "observational_only_not_predictive",
            "coverage_limited_to_selected_samples",
            "sparse_sample_fewer_than_three",
        ),
    }
    proof, receipt = _worker_proof(
        task,
        output,
        (ReadDependency("source_revision", str(citation.source_id), str(citation.revision_id)),),
    )
    prepared_value: JsonObject = {
        "prepared_scope": scope.to_json(),
        "prompt_projection": projection.to_json(),
    }
    proof = replace(
        proof,
        tool_outputs=(
            VerifiedToolOutput(
                "prepare_exam_sample_scope",
                "prepared_exam",
                "source.prepare_exam_sample_scope",
                "1.0.0",
                prepared_value,
                verified_child_value_fingerprint(prepared_value),
            ),
        ),
    )
    key_fingerprint = SHA_D
    commitment = ExamGeneratedBatchOwnerCommitment(
        request,
        key_fingerprint,
        scope,
        projection,
        mapping,
    )
    coordinator = exam_owner_coordinator_fingerprint(commitment, task, receipt, proof)
    owner = ExamGeneratedBatchOwnerReceipt.create(
        child_run_id=proof.run_id,
        child_task_id=task.task_id,
        child_task_fingerprint=task.fingerprint,
        child_receipt_fingerprint=receipt.fingerprint,
        child_proof_fingerprint=proof.fingerprint,
        task_bytes=task.to_bytes(),
        receipt_bytes=receipt.to_bytes(),
        request_bytes=request.to_bytes(),
        opaque_request_key_fingerprint=key_fingerprint,
        scope_fingerprint=scope.scope_fingerprint,
        projection_fingerprint=projection.projection_fingerprint,
        evidence_mapping_fingerprint=_mapping_fingerprint(mapping),
        coordinator_fingerprint=coordinator,
    )
    material = VerifiedExamOwnerMaterial(
        request,
        task,
        receipt,
        scope,
        projection,
        mapping,
        key_fingerprint,
        coordinator,
    )
    return owner, material, proof, context


class _Owners:
    def __init__(self, owner: GeneratedBatchOwnerReceipt) -> None:
        self.owner = owner

    def load(self, child_run_id: RunId) -> GeneratedBatchOwnerReceipt:
        assert child_run_id == self.owner.child_run_id
        return self.owner


class _Resolver:
    def __init__(
        self, material: VerifiedLessonOwnerMaterial | VerifiedExamOwnerMaterial
    ) -> None:
        self.material = material

    def resolve_lesson(
        self,
        owner: LessonGeneratedBatchOwnerReceipt,
        context: ExecutionContext,
    ) -> VerifiedLessonOwnerMaterial:
        assert isinstance(self.material, VerifiedLessonOwnerMaterial)
        return self.material

    def resolve_exam(
        self,
        owner: ExamGeneratedBatchOwnerReceipt,
        context: ExecutionContext,
    ) -> VerifiedExamOwnerMaterial:
        assert isinstance(self.material, VerifiedExamOwnerMaterial)
        return self.material


class _Proofs:
    def __init__(self, proof: VerifiedChildExecutionProof) -> None:
        self.proof = proof
        self.contexts: list[ExecutionContext] = []

    def load(
        self,
        task: GenerationWorkerTask,
        run_id: RunId,
        receipt: GenerationWorkerReceipt,
        context: ExecutionContext,
    ) -> VerifiedChildExecutionProof:
        self.contexts.append(context)
        return self.proof


def _adapter(
    owner: GeneratedBatchOwnerReceipt,
    material: VerifiedLessonOwnerMaterial | VerifiedExamOwnerMaterial,
    proof: VerifiedChildExecutionProof,
) -> tuple[VerifiedGeneratedBatchAdapter, _Proofs]:
    proofs = _Proofs(proof)
    return (
        VerifiedGeneratedBatchAdapter(
            owners=_Owners(owner), resolver=_Resolver(material), proofs=proofs
        ),
        proofs,
    )


def test_lesson_proof_converts_candidates_parents_and_nullable_model_receipt() -> None:
    owner, material, proof, context = _flashcard_fixture()
    adapter, proofs = _adapter(owner, material, proof)

    batch = adapter.recover(owner.child_run_id, context)

    assert len(batch.proposals) == 2
    first_content = batch.proposals[0].content.content
    second_content = batch.proposals[1].content.content
    assert isinstance(first_content, HybridFlashcardContent)
    assert isinstance(second_content, HybridFlashcardContent)
    assert first_content.parent_ordinal is None
    assert second_content.parent_ordinal == 0
    provenance = batch.proposals[0].provenance
    assert isinstance(provenance, GeneratedArtifactProvenance)
    assert provenance.model is not None
    assert provenance.model.response_id is None
    assert proofs.contexts == [generation_worker_child_context(material.task, context)]


def test_exam_proof_converts_observations_to_one_blueprint() -> None:
    owner, material, proof, context = _exam_fixture()
    adapter, _ = _adapter(owner, material, proof)

    batch = adapter.recover(owner.child_run_id, context)

    assert len(batch.proposals) == 1
    content = batch.proposals[0].content.content
    assert isinstance(content, ExamBlueprintContent)
    assert content.sample_size == 1
    assert content.observed_topics[0].source_commitment_indices == (0,)
    provenance = batch.proposals[0].provenance
    assert isinstance(provenance, GeneratedArtifactProvenance)
    assert provenance.profile_selection is None


def test_adapter_rejects_owner_proof_tamper_and_media_without_receipt() -> None:
    owner, material, proof, context = _flashcard_fixture()
    changed = replace(owner, child_task_fingerprint=SHA_D)
    adapter, _ = _adapter(changed, material, proof)
    with pytest.raises(VerifiedBatchRecoveryError, match="task or completed receipt"):
        adapter.recover(changed.child_run_id, context)

    owner, material, proof, context = _flashcard_fixture(media=True)
    adapter, _ = _adapter(owner, material, proof)
    with pytest.raises(UnsupportedVerifiedMediaError, match="media receipt"):
        adapter.recover(owner.child_run_id, context)


def test_adapter_rejects_parent_context_and_exam_coordinator_drift() -> None:
    lesson_owner, lesson_material, lesson_proof, lesson_context = _flashcard_fixture()
    lesson_adapter, _ = _adapter(lesson_owner, lesson_material, lesson_proof)
    with pytest.raises(VerifiedBatchRecoveryError, match="lesson coordinator"):
        lesson_adapter.recover(
            lesson_owner.child_run_id,
            replace(lesson_context, principal_id="another-service"),
        )

    owner, material, proof, context = _exam_fixture()
    changed = replace(material, coordinator_fingerprint=SHA_D)
    adapter, _ = _adapter(owner, changed, proof)
    with pytest.raises(VerifiedBatchRecoveryError, match="exam coordinator"):
        adapter.recover(owner.child_run_id, context)

    adapter, _ = _adapter(owner, material, proof)
    without_session = replace(context, session_id=None)
    with pytest.raises(VerifiedBatchRecoveryError, match="requires a session"):
        adapter.recover(owner.child_run_id, without_session)


class _OwnerStore:
    def __init__(self) -> None:
        self.values: dict[RunId, bytes] = {}

    def create(self, child_run_id: RunId, payload: bytes) -> bool:
        if child_run_id in self.values:
            return False
        self.values[child_run_id] = payload
        return True

    def load(self, child_run_id: RunId) -> bytes:
        return self.values[child_run_id]


def test_lesson_writer_loads_exact_child_context_and_publishes_once() -> None:
    owner, material, proof, context = _flashcard_fixture()
    store = _OwnerStore()
    proofs = _Proofs(proof)
    writer = VerifiedLessonOwnerWriterAdapter(proofs, GeneratedBatchOwnerRegistry(store))
    commitment = LessonGeneratedBatchOwnerCommitment(
        owner.lesson_run_id,
        owner.lesson_request_fingerprint,
        owner.lesson_plan_fingerprint,
        owner.lesson_profile_fingerprint,
        owner.coordinator_fingerprint,
        owner.page_position,
        owner.bundle_order,
        owner.bundle_id,
        owner.bundle_fingerprint,
        owner.wrapper_fingerprint,
        owner.scope_fingerprint,
        owner.read_set_fingerprint,
        owner.revision_commitments_fingerprint,
        owner.associated_overview_bundle_id,
        owner.overview_association_fingerprint,
    )

    first = writer.create(commitment, material.task, material.receipt, context)
    second = writer.create(commitment, material.task, material.receipt, context)

    assert first == second
    assert first.owner_receipt_fingerprint == owner.fingerprint
    assert proofs.contexts == [
        generation_worker_child_context(material.task, context),
        generation_worker_child_context(material.task, context),
    ]


def test_exam_writer_persists_exact_task_and_receipt_without_raw_key() -> None:
    owner, material, proof, context = _exam_fixture()
    store = _OwnerStore()
    registry = GeneratedBatchOwnerRegistry(store)
    writer = VerifiedExamOwnerWriterAdapter(registry)
    commitment = ExamGeneratedBatchOwnerCommitment(
        material.request,
        owner.opaque_request_key_fingerprint,
        material.prepared_scope,
        material.prompt_projection,
        material.evidence_mapping,
    )

    publication = writer.create(commitment, material.task, material.receipt, proof, context)
    stored = registry.load(publication.child_run_id)

    assert isinstance(stored, ExamGeneratedBatchOwnerReceipt)
    assert stored.task_bytes == material.task.to_bytes()
    assert stored.receipt_bytes == material.receipt.to_bytes()
    assert b"opaque-key-1" not in stored.to_bytes()


class _LessonStore:
    def __init__(self, material: VerifiedLessonOwnerMaterial) -> None:
        self.values = {str(material.checkpoint.run_id): material.checkpoint.to_bytes()}

    def load(self, key: str) -> bytes:
        return self.values[key]

    def create(self, key: str, payload: bytes) -> bool:
        if key in self.values:
            return False
        self.values[key] = payload
        return True

    def compare_and_set(self, key: str, expected: bytes, replacement: bytes) -> bool:
        if self.values[key] != expected:
            return False
        self.values[key] = replacement
        return True


class _LessonDetail:
    def __init__(self, material: VerifiedLessonOwnerMaterial, proof) -> None:  # type: ignore[no-untyped-def]
        self.material = material
        self.proof = proof

    def for_request(self, request):  # type: ignore[no-untyped-def]
        assert request == self.material.checkpoint.request
        return self

    def detail(self, task_id, prepared_scope_fingerprint, context):  # type: ignore[no-untyped-def]
        return VerifiedFlashcardPageResult(
            2,
            0,
            self.material.receipt.output_fingerprint,
            WorkerDetailView(self.material.receipt, self.proof.output),
        )


class _Content:
    def get_text(self, revision_id: RevisionId) -> str:
        raise AssertionError("not used")

    def resolve(self, citation):  # type: ignore[no-untyped-def]
        assert citation.quoted_snippet is not None
        return ResolvedCitation(citation, citation.quoted_snippet)


class _ExamScope:
    def __init__(self, material: VerifiedExamOwnerMaterial) -> None:
        self.material = material

    def prepare(self, request, context):  # type: ignore[no-untyped-def]
        return self.material.prepared_scope


def test_concrete_resolver_rebuilds_lesson_and_exam_material() -> None:
    lesson_owner, lesson_material, lesson_proof, lesson_context = _flashcard_fixture()
    exam_owner, exam_material, _, exam_context = _exam_fixture()
    resolver = VerifiedGeneratedOwnerResolverAdapter(
        lesson_store=_LessonStore(lesson_material),
        lesson_worker=_LessonDetail(lesson_material, lesson_proof),
        exam_scope=_ExamScope(exam_material),
        source_content=_Content(),
    )

    resolved_lesson = resolver.resolve_lesson(lesson_owner, lesson_context)
    resolved_exam = resolver.resolve_exam(exam_owner, exam_context)

    assert resolved_lesson.task == lesson_material.task
    assert resolved_exam.task == exam_material.task
    assert resolved_exam.coordinator_fingerprint == exam_owner.coordinator_fingerprint


class _Factory:
    def __init__(self, material: VerifiedExamOwnerMaterial) -> None:
        self.material = material

    def build(self, request, opaque_request_key):  # type: ignore[no-untyped-def]
        return self.material.task


class _ExamWorker:
    def __init__(self, material: VerifiedExamOwnerMaterial, proof) -> None:  # type: ignore[no-untyped-def]
        self.material = material
        self.proof = proof

    def detail(self, task_id, context):  # type: ignore[no-untyped-def]
        return WorkerDetailView(self.material.receipt, self.proof.output)


def test_exam_facade_publishes_owner_only_after_verified_mapping() -> None:
    _, material, proof, context = _exam_fixture()
    registry = GeneratedBatchOwnerRegistry(_OwnerStore())
    facade = ExamAnalysisFacade(
        cast(ExamAnalysisTaskFactory, _Factory(material)),
        cast(GenerationWorkerService, _ExamWorker(material, proof)),
        _Proofs(proof),
        VerifiedExamOwnerWriterAdapter(registry),
    )

    detail = facade.detail(material.request, "opaque-key-1", context)

    stored = registry.load(proof.run_id)
    assert isinstance(stored, ExamGeneratedBatchOwnerReceipt)
    assert detail.owner_publication.owner_receipt_fingerprint == stored.fingerprint
    assert stored.opaque_request_key_fingerprint == exam_opaque_request_key_fingerprint(
        "opaque-key-1"
    )
