from __future__ import annotations

import asyncio
from dataclasses import replace
from hashlib import sha256
from pathlib import Path
from typing import cast

from cardine.cli.registry import public_study_tool_entries
from cardine.cli.repository import LocalRepository, initialize_local_repository
from cardine.exams import (
    ExamAnalysisFacade,
    ExamAnalysisRequest,
    ExamPromptEvidenceProjection,
    PreparedExamSample,
    PreparedExamSampleScope,
)
from cardine.exams.analysis import ExamAnalysisTaskFactory, analyze_exam_sample_binding
from cardine.exams.worker import ExamAnalysisCompactView
from study_agent.adapters.filesystem import FilesystemExportWriter
from study_agent.application import ExportBundleV2, ExportService, ExportVersion
from study_agent.artifacts import (
    AnswerBlock,
    ArtifactService,
    ExamBlueprintContent,
    GeneratedArtifactProvenance,
    HumanAuthoredArtifactProvenance,
    ProjectionArtifactView,
    StudyArtifactEnvelope,
)
from study_agent.artifacts.candidates import (
    FlashcardAnswerBlock,
    FlashcardCandidate,
    FlashcardCandidateBatch,
    FlashcardPedagogicalRole,
)
from study_agent.artifacts.content import HybridFlashcardContent
from study_agent.artifacts.generated_owner import GeneratedBatchOwnerRegistry
from study_agent.artifacts.verified_batch import (
    VerifiedExamOwnerWriterAdapter,
    VerifiedGeneratedBatchAdapter,
    VerifiedGeneratedOwnerResolverAdapter,
    VerifiedLessonOwnerWriterAdapter,
)
from study_agent.capabilities.hybrid_flashcards import (
    HybridFlashcardTaskBinding,
    HybridPlannedBundleWorker,
    hybrid_flashcards_binding,
)
from study_agent.domain import (
    Actor,
    ArtifactDecision,
    ArtifactReadDependency,
    Citation,
    CorrelationId,
    CourseId,
    DomainEvent,
    ExecutionContext,
    HybridFlashcardRole,
    InteractionId,
    InteractionKind,
    PrincipalKind,
    RetrievalForm,
    RunId,
    SessionId,
    SourceCommitment,
    SourceId,
    StudyArtifactKind,
    session_event_id_for,
)
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerRequest,
    LessonWorkerStatus,
    ProfileTaskExpectation,
    ResolvedPlannedBundleEvidence,
    RevisionContentCommitment,
)
from study_agent.flashcards.lesson_worker_service import LessonWorkerService
from study_agent.flashcards.lesson_worker_view import LessonWorkerCompactView
from study_agent.flashcards.planning import (
    CanonicalSourceSpan,
    FlashcardLessonPlan,
    LessonGenerationUnit,
    LessonParagraph,
    LessonTopic,
    plan_flashcard_lesson,
)
from study_agent.grounding import EvidenceEnvelope, evidence_handle
from study_agent.pedagogy import (
    HYBRID_MACRO_DETAIL_V1,
    ProfileSelectionBasis,
    ProfileSelectionMode,
    ProfileSelectionReceipt,
    ProfileSelectorKind,
)
from study_agent.playbooks import (
    DialogueStep,
    PlaybookRunStatus,
    ReadDependency,
    ValidatorDisposition,
    VerifiedRunRecord,
)
from study_agent.playbooks.builtin.hybrid_flashcards_flow import HYBRID_FLASHCARDS_FLOW
from study_agent.ports import (
    EvidenceStatus,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    retrieval_read_set_fingerprint,
)
from study_agent.repository_config import EMPTY_CONFIG
from study_agent.sessions.events import SESSION_INTERACTION_RECORDED, interaction_recorded_payload
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.state import canonical_json_bytes
from study_agent.tools.builtin import public_study_tool_manifests
from study_agent.workers import (
    GenerationWorkerService,
    GenerationWorkerStatus,
    GenerationWorkerTaskKind,
    ValidationExpectation,
    ValidationReceiptSource,
    VerifiedPromptReceipt,
    fingerprint_output_schema,
)
from study_agent.workers.contracts import (
    ChildCapabilityObservation,
    ObservedValidationReceipt,
    fingerprint_execution_inputs,
    fingerprint_output,
)
from study_agent.workers.proof import (
    TechnicalModelReceipt,
    VerifiedChildExecutionProof,
    VerifiedToolOutput,
    verified_child_value_fingerprint,
)
from tests.course_fixtures import canonical_profile

COURSE = CourseId("course-1")
SESSION = SessionId("session-1")
SHA = "a" * 64
TRACE_SENTINELS = (
    "raw-body-sentinel",
    "principal-sentinel",
    "credential-sentinel",
    "scratch-sentinel",
    "response-id-sentinel",
    "candidate-sentinel",
)


def _context(
    kind: PrincipalKind, key: str, *, session: SessionId | None = SESSION
) -> ExecutionContext:
    return ExecutionContext(
        kind,
        "headless",
        COURSE,
        CorrelationId(key),
        frozenset({"source.read"}),
        session,
        idempotency_key=key,
    )


def _compact_host_trace(
    *,
    lesson: LessonWorkerCompactView | None = None,
    exam: ExamAnalysisCompactView | None = None,
) -> bytes:
    """Allowlisted host trace built only from bounded compact contracts."""

    value: dict[str, JsonValue] = {"schema": "headless-host-trace@1"}
    if lesson is not None:
        value["lesson"] = {
            "run_id": str(lesson.run_id),
            "plan_fingerprint": lesson.plan_fingerprint,
            "profile_fingerprint": lesson.profile_fingerprint,
            "status": lesson.status.value,
            "completed_positions": lesson.completed_positions,
            "failed_positions": lesson.failed_positions,
            "pending_positions": lesson.pending_positions,
            "candidate_count": lesson.candidate_count,
            "omission_count": lesson.omission_count,
            "failure_codes": lesson.failure_codes,
            "in_progress": lesson.in_progress,
            "advance_required": lesson.advance_required,
        }
    if exam is not None:
        value["exam"] = {
            "task_id": exam.task_id,
            "run_id": str(exam.run_id) if exam.run_id is not None else None,
            "status": exam.status.value,
            "sample_size": exam.sample_size,
            "topic_count": exam.topic_count,
            "format_count": exam.format_count,
            "evidence_handle_count": exam.evidence_coverage_count,
            "limitation_codes": exam.limitation_codes,
            "detail_available": exam.detail_available,
        }
    encoded = canonical_json_bytes(freeze_object(value))
    if len(encoded) > 8192:
        raise AssertionError("host-visible compact trace exceeded 8 KiB")
    return encoded


def _assert_redacted_compact_trace(encoded: bytes) -> None:
    for sentinel in TRACE_SENTINELS:
        assert sentinel.encode() not in encoded
    assert b"raw_body" not in encoded
    assert b"credential" not in encoded
    assert b"response_id" not in encoded


def _assert_opaque_evidence_handles(
    actual: tuple[str, ...], expected: tuple[str, ...], raw_source: str
) -> None:
    assert actual == expected
    for handle in actual:
        assert handle.startswith("ev_")
        assert len(handle) == 67
        assert all(char in "0123456789abcdef" for char in handle[3:])
        assert raw_source not in handle
        assert all(sentinel not in handle for sentinel in TRACE_SENTINELS)


class _Store:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

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

    def load(self, key: str) -> bytes:
        return self.values[key]


class _ScriptedCompletedRuns:
    """Typed, provider-neutral child port returning one deterministic batch."""

    def __init__(
        self,
        exam_output: JsonObject | None = None,
        *,
        read_dependencies: tuple[ReadDependency, ...] | None = None,
    ) -> None:
        self.calls: list[str] = []
        self.exam_output = exam_output
        self.read_dependencies = read_dependencies or (
            ReadDependency("course", str(COURSE), "source"),
        )
        self.proofs: dict[RunId, VerifiedChildExecutionProof] = {}

    async def start(self, task, context):  # type: ignore[no-untyped-def]
        del context
        self.calls.append(task.task_id)
        if task.task_kind.value == "exam_analysis":
            if self.exam_output is None:
                raise AssertionError("exam scripted output was not configured")
            output = self.exam_output
        else:
            handle = task.evidence_references[0]
            output = FlashcardCandidateBatch(
                (
                    FlashcardCandidate(
                        "candidate-aortic-cusps",
                        None,
                        RetrievalForm.DIRECT_RECALL,
                        "How many cusps does the aortic valve have?",
                        (FlashcardAnswerBlock("Answer", "Three cusps", ()),),
                        FlashcardPedagogicalRole.SECTION,
                        None,
                        None,
                        "Directly grounded in the lesson paragraph; candidate-sentinel.",
                        (handle,),
                        (),
                    ),
                ),
                (),
            ).to_json()
        validations = tuple(
            ObservedValidationReceipt(
                item.step_id,
                item.source,
                item.validator_id,
                item.validator_version,
                True,
                SHA,
                ValidatorDisposition.CONTINUE,
            )
            for item in task.expected_validations
        )
        run_id = RunId("scripted-child-" + task.task_id[-12:])
        run = VerifiedRunRecord(
            run_id,
            task.definition_fingerprint,
            task.capability_inputs(),
            task.pins,
            self.read_dependencies,
            {"candidate_batch": output},
            (),
            PlaybookRunStatus.COMPLETED,
        )
        self.proofs[run_id] = VerifiedChildExecutionProof(
            run_id,
            GenerationWorkerStatus.COMPLETED,
            task.definition_fingerprint,
            task.pins,
            task.payload_fingerprint,
            output,
            fingerprint_output(output),
            run.read_dependencies,
            (),
            TechnicalModelReceipt(
                "scripted-adapter", "1.0.0", "scripted-model", "response-id-sentinel"
            ),
            VerifiedPromptReceipt(task.pins.prompt.id, str(task.pins.prompt.version), SHA),
            validations,
            fingerprint_execution_inputs(task.capability_inputs()),
        )
        return ChildCapabilityObservation(
            GenerationWorkerStatus.COMPLETED,
            task.capability_id,
            task.capability_version,
            task.manifest_fingerprint,
            run_id,
            task.pins,
            task.definition_fingerprint,
            task.output_schema_fingerprint,
            validations,
            VerifiedPromptReceipt(task.pins.prompt.id, str(task.pins.prompt.version), SHA),
            verified_run=run,
            output=output,
            execution_input_fingerprint=fingerprint_execution_inputs(task.capability_inputs()),
        )

    async def resume(self, task, continuation, response, context):  # type: ignore[no-untyped-def]
        del continuation, response
        return await self.start(task, context)  # type: ignore[no-untyped-call]


class _IngestedBundleResolver:
    def __init__(self, content, source):  # type: ignore[no-untyped-def]
        self.content = content
        self.source = source
        self.calls = 0

    def resolve(self, plan, bundle, revision_commitments, context):  # type: ignore[no-untyped-def]
        del context
        self.calls += 1
        evidence: list[RetrievalEvidence] = []
        text = self.content.get_text(self.source.source.revision_id)
        for slot in bundle.slots:
            chunk = next(
                item
                for item in self.source.chunks
                if item.source_id == slot.span.source_id
                and item.revision_id == slot.span.revision_id
                and item.start_offset == slot.span.start_offset
                and item.end_offset == slot.span.end_offset
            )
            quoted = text[chunk.start_offset : chunk.end_offset]
            citation = self.content.resolve(
                Citation(
                    chunk.source_id,
                    chunk.revision_id,
                    chunk.chunk_id,
                    chunk.start_offset,
                    chunk.end_offset,
                    slot.span.locator,
                    quoted,
                )
            ).citation
            evidence.append(RetrievalEvidence(chunk, citation, quoted, 1.0))
        ordered = tuple(evidence)
        envelope = EvidenceEnvelope.from_retrieval(
            RetrievalEvidenceSet(
                EvidenceStatus.SUFFICIENT,
                ordered,
                sha256(plan.plan_fingerprint.encode()).hexdigest(),
                "ingested-chunk",
                "1.0.0",
                "canonical-source",
                retrieval_read_set_fingerprint(ordered),
            )
        )
        return ResolvedPlannedBundleEvidence(
            envelope,
            revision_commitments,
            plan.plan_fingerprint,
            bundle.bundle_id,
        )


class _ScriptedProofReader:
    """In-memory proof port for the deterministic child adapter fixture."""

    def __init__(self, proofs: dict[RunId, VerifiedChildExecutionProof]) -> None:
        self.proofs = proofs
        self.calls: list[RunId] = []

    def load(  # type: ignore[no-untyped-def]
        self, task, run_id, receipt, context, execution_inputs=None
    ):
        del task, receipt, context, execution_inputs
        self.calls.append(run_id)
        return self.proofs[run_id]


class _ExamProofReader:
    def __init__(self, scope: PreparedExamSampleScope, output: JsonObject) -> None:
        self.scope = scope
        self.output = output

    def load(  # type: ignore[no-untyped-def]
        self, task, run_id, receipt, context, execution_inputs=None
    ):
        del context, execution_inputs
        validations = tuple(
            ObservedValidationReceipt(
                item.step_id,
                item.source,
                item.validator_id,
                item.validator_version,
                True,
                SHA,
                ValidatorDisposition.CONTINUE,
            )
            for item in task.expected_validations
        )
        projection = ExamPromptEvidenceProjection.from_scope(self.scope)
        prepared = {
            "prepared_scope": self.scope.to_json(),
            "prompt_projection": projection.to_json(),
        }
        return VerifiedChildExecutionProof(
            run_id,
            GenerationWorkerStatus.COMPLETED,
            task.definition_fingerprint,
            task.pins,
            receipt.input_fingerprint,
            self.output,
            fingerprint_output(self.output),
            (
                ReadDependency(
                    "source_revision",
                    str(self.scope.samples[0].source_id),
                    str(self.scope.samples[0].revision_id),
                ),
            ),
            (
                VerifiedToolOutput(
                    "prepare_exam_sample_scope",
                    "prepared_exam",
                    "source.prepare_exam_sample_scope",
                    "1.0.0",
                    prepared,
                    verified_child_value_fingerprint(prepared),
                ),
            ),
            TechnicalModelReceipt(
                "scripted-adapter", "1.0.0", "scripted-model", "response-id-sentinel"
            ),
            VerifiedPromptReceipt(task.pins.prompt.id, str(task.pins.prompt.version), SHA),
            validations,
            fingerprint_execution_inputs(task.capability_inputs()),
        )


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


class _LessonWorkerRouter:
    def __init__(self, worker: HybridPlannedBundleWorker) -> None:
        self.worker = worker

    def for_request(self, request):  # type: ignore[no-untyped-def]
        del request
        return self.worker


class _ExamScopePort:
    def __init__(self, scope: PreparedExamSampleScope) -> None:
        self.scope = scope

    def prepare(self, request, context):  # type: ignore[no-untyped-def]
        del context
        if tuple(item.revision_id for item in self.scope.samples) != request.sample_revision_ids:
            raise AssertionError("exam scope request changed")
        return self.scope


class _UnusedExamScopePort:
    def prepare(self, request, context):  # type: ignore[no-untyped-def]
        del request, context
        raise AssertionError("exam scope is not used by the lesson recovery path")


class _UnusedLessonWorkerRouter:
    def for_request(self, request):  # type: ignore[no-untyped-def]
        del request
        raise AssertionError("lesson worker is not used by the exam recovery path")


class _ProofReaderRouter:
    def __init__(self, lesson, exam) -> None:  # type: ignore[no-untyped-def]
        self.lesson = lesson
        self.exam = exam

    def load(  # type: ignore[no-untyped-def]
        self, task, run_id, receipt, context, execution_inputs=None
    ):
        if task.task_kind is GenerationWorkerTaskKind.EXAM_ANALYSIS:
            return self.exam.load(task, run_id, receipt, context, execution_inputs)
        return self.lesson.load(task, run_id, receipt, context, execution_inputs)


class _SourceCommitments:
    def __init__(self, values: tuple[SourceCommitment, ...]) -> None:
        self.values = frozenset(values)

    def contains(self, course_id: CourseId, commitment: SourceCommitment) -> bool:
        return course_id == COURSE and commitment in self.values


class _NoPolicy:
    def decide(self, request):  # type: ignore[no-untyped-def]
        raise AssertionError("service policy is not used")


def _bundle_json(bundle: ExportBundleV2) -> JsonObject:
    return {
        "course_id": str(bundle.course_id),
        "high_water_sequence": bundle.high_water_sequence,
        "course": bundle.course,
        "sources": bundle.sources,
        "sessions": bundle.sessions,
        "answers": bundle.answers,
        "events": bundle.events,
        "artifacts": bundle.artifacts,
    }


def _profile_expectation() -> ProfileTaskExpectation:
    binding = hybrid_flashcards_binding(
        dependency_resolver=lambda *, context, inputs: (
            ReadDependency("course", str(context.course_id), "source"),
        ),
        model_adapter=ArtifactReference("scripted-adapter", SemanticVersion.parse("1.0.0")),
        state_contract=ArtifactReference("state", SemanticVersion.parse("1.0.0")),
    )
    return ProfileTaskExpectation(
        ProfileSelectionReceipt(
            HYBRID_MACRO_DETAIL_V1,
            ProfileSelectionMode.DEFAULT,
            ProfileSelectorKind.HOST,
            PrincipalKind.SERVICE,
            ProfileSelectionBasis(),
        ),
        binding.manifest.id,
        binding.manifest.version,
        binding.manifest_fingerprint,
        binding.manifest.required_authority,
        binding.pins,
        __import__("study_agent.playbooks", fromlist=["playbook_definition_fingerprint"])
        .playbook_definition_fingerprint(binding.playbook),
        binding.manifest.output_schema,
        fingerprint_output_schema(binding.manifest.output_schema),
        (
            ValidationExpectation(
                "check_hybrid_readiness",
                ValidationReceiptSource.VALIDATE_STEP,
                "hybrid_flashcards_readiness",
                "1.0.0",
            ),
            ValidationExpectation(
                "generate_hybrid_flashcards",
                ValidationReceiptSource.STRUCTURED_OUTPUT_FALLBACK,
                "hybrid_flashcards_integrity",
                "1.0.0",
            ),
            ValidationExpectation(
                "validate_hybrid_flashcards",
                ValidationReceiptSource.VALIDATE_STEP,
                "hybrid_flashcards_integrity",
                "1.0.0",
            ),
        ),
    )


def test_headless_public_workers_and_artifacts_replay_identically(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            canonical_profile(COURSE), _context(PrincipalKind.SERVICE, "course", session=None)
        )
        lesson = repository.for_course(COURSE).ingestion.ingest(
            filename="lesson.md",
            content=(
                b"Aortic valve has three cusps. raw-body-sentinel principal-sentinel "
                b"credential-sentinel scratch-sentinel."
            ),
            source_id=SourceId("lesson"),
            title="Lesson",
            trust_level=90,
            source_role="primary",
            context=_context(PrincipalKind.SERVICE, "lesson-ingest", session=None),
        )
        repository.session_service.start(_context(PrincipalKind.SERVICE, "session-start"))
        span = CanonicalSourceSpan(
            lesson.source.source_id,
            lesson.source.revision_id,
            lesson.chunks[0].start_offset,
            lesson.chunks[0].end_offset,
            "Lesson · chunk 1 · chars "
            f"{lesson.chunks[0].start_offset}-{lesson.chunks[0].end_offset}",
        )
        paragraph = LessonParagraph(
            "p-cusps", "valve", 0, span, span.end_offset - span.start_offset
        )
        plan: FlashcardLessonPlan = plan_flashcard_lesson(
            LessonGenerationUnit(
                "lesson",
                "Lesson",
                (
                    LessonTopic(
                        "valve", "Aortic valve", 1, None, 0, span, (paragraph.paragraph_key,)
                    ),
                ),
                (paragraph,),
            )
        )
        assert len(plan.bundles) == 1
        assert plan.to_bytes() == plan_flashcard_lesson(
            LessonGenerationUnit(
                "lesson",
                "Lesson",
                (
                    LessonTopic(
                        "valve", "Aortic valve", 1, None, 0, span, (paragraph.paragraph_key,)
                    ),
                ),
                (paragraph,),
            )
        ).to_bytes()
        expectation = _profile_expectation()
        request = LessonWorkerRequest(
            plan,
            "Generate grounded cards",
            "the uploaded lesson",
            "en",
            4,
            None,
            expectation,
            1,
            (RevisionContentCommitment(lesson.source.revision_id, lesson.source.checksum_sha256),),
        )
        profile_binding = hybrid_flashcards_binding(
            dependency_resolver=lambda *, context, inputs: (
                ReadDependency("course", str(context.course_id), "source"),
            ),
            model_adapter=ArtifactReference("scripted-adapter", SemanticVersion.parse("1.0.0")),
            state_contract=ArtifactReference("state", SemanticVersion.parse("1.0.0")),
        )
        task_binding = HybridFlashcardTaskBinding(request, profile_binding)
        lesson_source_dependency = ReadDependency(
            "source_revision", str(lesson.source.source_id), str(lesson.source.revision_id)
        )
        child_runs = _ScriptedCompletedRuns(read_dependencies=(lesson_source_dependency,))
        generation_store = _Store()
        generation = GenerationWorkerService(store=generation_store, isolated_runs=child_runs)
        planned_worker = HybridPlannedBundleWorker(request, task_binding, generation)
        resolver = _IngestedBundleResolver(  # type: ignore[no-untyped-call]
            repository.for_course(COURSE).content, lesson
        )
        lesson_store = _Store()
        owner_store = _OwnerStore()
        owners = GeneratedBatchOwnerRegistry(owner_store)
        lesson_proofs = _ScriptedProofReader(child_runs.proofs)
        lesson_owner_writer = VerifiedLessonOwnerWriterAdapter(lesson_proofs, owners)
        lesson_service = LessonWorkerService(
            store=lesson_store,
            resolver=resolver,
            task_binding=task_binding,
            worker=planned_worker,
            owner_writer=lesson_owner_writer,
        )
        worker_context = ExecutionContext(
            PrincipalKind.SERVICE,
            "principal-sentinel",
            COURSE,
            CorrelationId("lesson-worker"),
            frozenset(expectation.required_authority),
            SESSION,
            idempotency_key="lesson-worker",
        )
        compact = asyncio.run(
            lesson_service.start(request, worker_context)
        )
        assert compact.status is LessonWorkerStatus.COMPLETED
        lesson_trace = _compact_host_trace(lesson=compact)
        _assert_redacted_compact_trace(lesson_trace)
        retried = asyncio.run(lesson_service.start(request, worker_context))
        assert retried == compact
        detail = lesson_service.review_page(compact.run_id, request, 0, worker_context)
        reviewed = lesson_service.review_completed(compact.run_id, request, worker_context)
        assert lesson_service.review_completed(compact.run_id, request, worker_context) == reviewed
        assert detail.detail.output == reviewed.pages[0].batch.to_json()
        assert detail.read_set_fingerprint == reviewed.pages[0].read_set_fingerprint
        _assert_opaque_evidence_handles(
            reviewed.pages[0].batch.candidates[0].evidence_ids,
            (evidence_handle(lesson.chunks[0].chunk_id),),
            "Aortic valve has three cusps",
        )
        assert resolver.calls == 1
        assert len(child_runs.calls) == 1
        lesson_owner = owners.load(next(iter(child_runs.proofs)))
        assert lesson_owner.child_run_id in child_runs.proofs

        # Simulate process loss: rebuild every worker/owner/proof adapter over
        # the same canonical byte stores. The new isolated port must not call
        # the model again for the already-terminal child task.
        reloaded_child_runs = _ScriptedCompletedRuns(
            read_dependencies=(lesson_source_dependency,)
        )
        reloaded_generation = GenerationWorkerService(
            store=generation_store, isolated_runs=reloaded_child_runs
        )
        reloaded_planned_worker = HybridPlannedBundleWorker(
            request, task_binding, reloaded_generation
        )
        reloaded_resolver = _IngestedBundleResolver(  # type: ignore[no-untyped-call]
            repository.for_course(COURSE).content, lesson
        )
        reloaded_proofs = _ScriptedProofReader(child_runs.proofs)
        reloaded_lesson_service = LessonWorkerService(
            store=lesson_store,
            resolver=reloaded_resolver,
            task_binding=task_binding,
            worker=reloaded_planned_worker,
            owner_writer=VerifiedLessonOwnerWriterAdapter(reloaded_proofs, owners),
        )
        reloaded_compact = asyncio.run(
            reloaded_lesson_service.start(request, worker_context)
        )
        assert reloaded_compact == compact
        assert reloaded_child_runs.calls == []

        commitment = SourceCommitment(
            lesson.source.source_id,
            lesson.source.revision_id,
            lesson.chunks[0].chunk_id,
            lesson.chunks[0].start_offset,
            lesson.chunks[0].end_offset,
        )
        run_id = lesson_owner.child_run_id
        recovery_resolver = VerifiedGeneratedOwnerResolverAdapter(
            lesson_store=lesson_store,
            lesson_worker=_LessonWorkerRouter(reloaded_planned_worker),
            exam_scope=_UnusedExamScopePort(),
            source_content=repository.for_course(COURSE).content,
        )
        reloaded_port = VerifiedGeneratedBatchAdapter(
            owners=owners,
            resolver=recovery_resolver,
            proofs=reloaded_proofs,
        )
        reloaded_artifacts = ArtifactService(
            repository.events,
            repository.clock,
            ProjectionArtifactView(repository.events.projection),
            repository.sessions,
            reloaded_port,
            _SourceCommitments((commitment,)),
            _NoPolicy(),
        )
        port = VerifiedGeneratedBatchAdapter(
            owners=owners,
            resolver=recovery_resolver,
            proofs=lesson_proofs,
        )
        artifacts = ArtifactService(
            repository.events,
            repository.clock,
            ProjectionArtifactView(repository.events.projection),
            repository.sessions,
            port,
            _SourceCommitments((commitment,)),
            _NoPolicy(),
        )
        before = len(repository.events.read(COURSE))
        snapshot = artifacts.record_generated(run_id, worker_context, before)
        after_generated = len(repository.events.read(COURSE))
        assert reloaded_artifacts.record_generated(run_id, worker_context, before) == snapshot
        assert len(repository.events.read(COURSE)) == after_generated
        recovered = port.recover(run_id, worker_context)
        candidate = reviewed.pages[0].batch.candidates[0]
        recovered_proposal = recovered.proposals[0]
        assert isinstance(recovered_proposal.content.content, HybridFlashcardContent)
        assert isinstance(recovered_proposal.provenance, GeneratedArtifactProvenance)
        assert recovered_proposal.content.content.prompt == candidate.prompt
        assert (
            recovered_proposal.content.content.answer_blocks[0].text
            == candidate.answer_blocks[0].text
        )
        assert recovered_proposal.content.content.rationale == candidate.rationale
        assert recovered_proposal.provenance.source_commitments == (commitment,)
        assert recovered_proposal.provenance.run_id == run_id
        assert recovered_proposal.provenance.output_fingerprint == sha256(
            recovered_proposal.content.to_bytes()
        ).hexdigest()
        human = ExecutionContext(
            PrincipalKind.HUMAN,
            "student",
            COURSE,
            CorrelationId("human-review"),
            frozenset({"source.read"}),
            SESSION,
            idempotency_key="human-review",
        )
        generated_revision = snapshot.pending()[0]
        accept_expected = len(repository.events.read(COURSE))
        accepted = artifacts.record_human_decision(
            generated_revision.id,
            ArtifactDecision.ACCEPT,
            None,
            human,
            accept_expected,
        )
        after_accept = len(repository.events.read(COURSE))
        assert reloaded_artifacts.record_human_decision(
            generated_revision.id,
            ArtifactDecision.ACCEPT,
            None,
            human,
            accept_expected,
        ) == accepted
        assert len(repository.events.read(COURSE)) == after_accept
        interaction_id = InteractionId("human-review-interaction")
        sequence = len(repository.events.read(COURSE))
        interaction_event = DomainEvent(
            session_event_id_for(
                COURSE,
                SESSION,
                RunId("human-review"),
                "human-review",
                SESSION_INTERACTION_RECORDED,
            ),
            COURSE,
            sequence + 1,
            SESSION_INTERACTION_RECORDED,
            1,
            Actor(PrincipalKind.HUMAN, "student"),
            repository.clock.now(),
            CorrelationId("human-review"),
            interaction_recorded_payload(
                interaction_id,
                InteractionKind.HUMAN,
                "I reviewed the generated card and will revise its wording.",
            ),
            SESSION,
        )
        repository.events.append(COURSE, sequence, (interaction_event,))
        revised_content = StudyArtifactEnvelope(
            StudyArtifactKind.FLASHCARD,
            HybridFlashcardContent(
                RetrievalForm.DIRECT_RECALL,
                "How many cusps does the aortic valve have?",
                (AnswerBlock("Answer", "Human-verified: three cusps", ()),),
                HybridFlashcardRole.DETAIL,
                "Human wording revision grounded in the same source.",
                (0,),
            ),
        )
        human_provenance = HumanAuthoredArtifactProvenance(
            PrincipalKind.HUMAN,
            interaction_id,
            (commitment,),
            (
                ArtifactReadDependency(
                    "source_revision",
                    str(commitment.source_id),
                    str(commitment.revision_id),
                ),
            ),
            accepted.accepted()[0].id,
        )
        revision_expected = len(repository.events.read(COURSE))
        revised = artifacts.record_human_revision(
            revised_content,
            human_provenance,
            accepted.accepted()[0].artifact_id,
            human,
            revision_expected,
        )
        after_revision = len(repository.events.read(COURSE))
        assert reloaded_artifacts.record_human_revision(
            revised_content,
            human_provenance,
            accepted.accepted()[0].artifact_id,
            human,
            revision_expected,
        ) == revised
        assert len(repository.events.read(COURSE)) == after_revision
        revised_pending = revised.pending()[0]
        reject_context = replace(human, idempotency_key="human-reject")
        reject_expected = len(repository.events.read(COURSE))
        rejected = artifacts.record_human_decision(
            revised_pending.id,
            ArtifactDecision.REJECT,
            None,
            reject_context,
            reject_expected,
        )
        after_reject = len(repository.events.read(COURSE))
        assert reloaded_artifacts.record_human_decision(
            revised_pending.id,
            ArtifactDecision.REJECT,
            None,
            reject_context,
            reject_expected,
        ) == rejected
        assert len(repository.events.read(COURSE)) == after_reject
        assert len(accepted.accepted()) == 1
        assert rejected.decisions[-1].decision is ArtifactDecision.REJECT
        assert len(rejected.pending()) == 0
        exported = ExportService(repository.events).assemble(COURSE, version=ExportVersion.V2)
        assert isinstance(exported, ExportBundleV2) and len(exported.artifacts) == 2
        FilesystemExportWriter().write(exported, tmp_path / "export")
        exported_bytes = canonical_json_bytes(_bundle_json(exported))
        assert str(lesson.source.source_id).encode() in exported_bytes
        assert str(lesson.source.revision_id).encode() in exported_bytes
        assert repository.events.verify_projection(COURSE)

    with LocalRepository.open(root) as reopened:
        replayed = ExportService(reopened.events).assemble(COURSE, version=ExportVersion.V2)
        assert isinstance(replayed, ExportBundleV2)
        assert canonical_json_bytes(_bundle_json(replayed)) == exported_bytes
        assert reopened.events.verify_projection(COURSE)


def test_headless_public_exam_analysis_completes_and_publishes_exact_mapping(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            canonical_profile(COURSE), _context(PrincipalKind.SERVICE, "course", session=None)
        )
        exam = repository.for_course(COURSE).ingestion.ingest(
            filename="exam.md",
            content=(
                b"Describe the aortic valve and its three cusps. raw-body-sentinel "
                b"principal-sentinel credential-sentinel scratch-sentinel."
            ),
            source_id=SourceId("exam"),
            title="Exam",
            trust_level=90,
            source_role="exam_sample",
            context=_context(PrincipalKind.SERVICE, "exam-ingest", session=None),
        )
        content = repository.for_course(COURSE).content
        chunk = exam.chunks[0]
        text = content.get_text(exam.source.revision_id)
        citation = content.resolve(
            Citation(
                chunk.source_id,
                chunk.revision_id,
                chunk.chunk_id,
                chunk.start_offset,
                chunk.end_offset,
                f"Exam · chunk 1 · chars {chunk.start_offset}-{chunk.end_offset}",
                text[chunk.start_offset : chunk.end_offset],
            )
        ).citation
        evidence = RetrievalEvidence(
            chunk,
            citation,
            text[chunk.start_offset : chunk.end_offset],
            1.0,
        )
        envelope = EvidenceEnvelope.from_retrieval(
            RetrievalEvidenceSet(
                EvidenceStatus.SUFFICIENT,
                (evidence,),
                SHA,
                "ingested-exam",
                "1.0.0",
                "canonical-source",
                retrieval_read_set_fingerprint((evidence,)),
            )
        )
        scope = PreparedExamSampleScope.prepare(
            (
                PreparedExamSample(
                    "sample-aortic",
                    COURSE,
                    exam.source.source_id,
                    exam.source.revision_id,
                    "exam_sample",
                    True,
                    len(text),
                    (envelope.items[0].handle,),
                ),
            ),
            envelope,
        )
        handle = envelope.items[0].handle
        output: JsonObject = {
            "sample_size": 1,
            "observed_topics": ({"value": "Aortic valve", "evidence_ids": (handle,)},),
            "observed_formats": ({"value": "Open response", "evidence_ids": (handle,)},),
            "limitations": (
                "observational_only_not_predictive",
                "coverage_limited_to_selected_samples",
                "sparse_sample_fewer_than_three",
            ),
        }
        binding = analyze_exam_sample_binding(
            dependency_resolver=lambda *, context, inputs: (
                ReadDependency("course", str(context.course_id), "source"),
            ),
            model_adapter=ArtifactReference("scripted-adapter", SemanticVersion.parse("1.0.0")),
            state_contract=ArtifactReference("state", SemanticVersion.parse("1.0.0")),
        )
        request = ExamAnalysisRequest((exam.source.revision_id,), "en")
        factory = ExamAnalysisTaskFactory(binding)
        task = factory.build(request, "opaque-exam-key")
        context = ExecutionContext(
            PrincipalKind.SERVICE,
            "principal-sentinel",
            COURSE,
            CorrelationId("exam-analysis"),
            frozenset(task.required_authority),
            SESSION,
            idempotency_key="exam-analysis",
        )
        repository.session_service.start(_context(PrincipalKind.SERVICE, "session-start"))
        source_dependency = ReadDependency(
            "source_revision", str(exam.source.source_id), str(exam.source.revision_id)
        )
        scripted = _ScriptedCompletedRuns(output, read_dependencies=(source_dependency,))
        worker = GenerationWorkerService(store=_Store(), isolated_runs=scripted)
        owner_store = _OwnerStore()
        owners = GeneratedBatchOwnerRegistry(owner_store)
        exam_proofs = _ExamProofReader(scope, output)
        facade = ExamAnalysisFacade(
            factory,
            worker,
            exam_proofs,
            VerifiedExamOwnerWriterAdapter(owners),
        )
        compact = asyncio.run(facade.start(request, "opaque-exam-key", context))
        assert compact.status is GenerationWorkerStatus.COMPLETED
        exam_trace = _compact_host_trace(exam=compact)
        _assert_redacted_compact_trace(exam_trace)
        assert compact.topic_count == 1
        assert compact.format_count == 1
        assert compact.evidence_coverage_count == 1
        detail = facade.detail(request, "opaque-exam-key", context)
        assert detail.proposal.to_json() == output
        exam_handles = tuple(
            evidence_id
            for observation in (
                *detail.proposal.observed_topics,
                *detail.proposal.observed_formats,
            )
            for evidence_id in observation.evidence_ids
        )
        _assert_opaque_evidence_handles(
            exam_handles,
            (handle, handle),
            "Describe the aortic valve and its three cusps",
        )
        assert len(detail.evidence_mapping) == 1
        mapping = detail.evidence_mapping[0]
        assert mapping.source_id == exam.source.source_id
        assert mapping.revision_id == exam.source.revision_id
        assert mapping.chunk_id == chunk.chunk_id
        assert mapping.start_offset == chunk.start_offset
        assert mapping.end_offset == chunk.end_offset
        assert (
            detail.owner_publication.child_proof_fingerprint
            == detail.proof_reference.proof_fingerprint
        )
        asyncio.run(facade.start(request, "opaque-exam-key", context))
        assert len(scripted.calls) == 1
        stored_owner = owners.load(detail.owner_publication.child_run_id)
        assert stored_owner.fingerprint == detail.owner_publication.owner_receipt_fingerprint

        commitment = SourceCommitment(
            exam.source.source_id,
            exam.source.revision_id,
            chunk.chunk_id,
            chunk.start_offset,
            chunk.end_offset,
        )
        artifact_run_id = detail.owner_publication.child_run_id
        recovery_resolver = VerifiedGeneratedOwnerResolverAdapter(
            lesson_store=_Store(),
            lesson_worker=_UnusedLessonWorkerRouter(),
            exam_scope=_ExamScopePort(scope),
            source_content=repository.for_course(COURSE).content,
        )
        generated_port = VerifiedGeneratedBatchAdapter(
            owners=owners,
            resolver=recovery_resolver,
            proofs=_ProofReaderRouter(_ScriptedProofReader(scripted.proofs), exam_proofs),
        )
        artifacts = ArtifactService(
            repository.events,
            repository.clock,
            ProjectionArtifactView(repository.events.projection),
            repository.sessions,
            generated_port,
            _SourceCommitments((commitment,)),
            _NoPolicy(),
        )
        before_generated = len(repository.events.read(COURSE))
        artifact_context = replace(
            context,
            correlation_id=CorrelationId("exam-artifact"),
            idempotency_key="exam-artifact",
        )
        generated = artifacts.record_generated(artifact_run_id, artifact_context, before_generated)
        assert len(repository.events.read(COURSE)) == before_generated + 1
        assert artifacts.record_generated(
            artifact_run_id, artifact_context, before_generated
        ) == generated
        assert len(repository.events.read(COURSE)) == before_generated + 1
        recovered = generated_port.recover(artifact_run_id, artifact_context)
        recovered_proposal = recovered.proposals[0]
        recovered_content = recovered_proposal.content.content
        assert isinstance(recovered_content, ExamBlueprintContent)
        assert isinstance(recovered_proposal.provenance, GeneratedArtifactProvenance)
        assert recovered_content.sample_size == detail.proposal.sample_size
        assert (
            recovered_content.observed_topics[0].value
            == detail.proposal.observed_topics[0].value
        )
        assert recovered_proposal.provenance.source_commitments == (commitment,)
        assert recovered_proposal.provenance.retrieval.read_set_fingerprint == (
            scope.evidence.read_set_fingerprint
        )
        assert recovered_proposal.provenance.run_id == artifact_run_id

        human_context = replace(
            artifact_context,
            principal_kind=PrincipalKind.HUMAN,
            principal_id="student",
            correlation_id=CorrelationId("exam-human-accept"),
            idempotency_key="exam-human-accept",
        )
        pending = generated.pending()[0]
        accepted = artifacts.record_human_decision(
            pending.id,
            ArtifactDecision.ACCEPT,
            None,
            human_context,
            len(repository.events.read(COURSE)),
        )
        after_accept = len(repository.events.read(COURSE))
        assert after_accept == before_generated + 2
        assert len(accepted.accepted()) == 1
        assert artifacts.record_human_decision(
            pending.id,
            ArtifactDecision.ACCEPT,
            None,
            human_context,
            before_generated + 1,
        ) == accepted
        assert len(repository.events.read(COURSE)) == after_accept

        exported = ExportService(repository.events).assemble(COURSE, version=ExportVersion.V2)
        assert isinstance(exported, ExportBundleV2)
        assert len(exported.artifacts) == 1
        FilesystemExportWriter().write(exported, tmp_path / "exam-export")
        exported_bytes = canonical_json_bytes(_bundle_json(exported))
        assert len(exported_bytes) < 512 * 1024
        assert b"opaque-exam-key" not in exported_bytes
        assert str(artifact_run_id).encode() in exported_bytes
        assert str(exam.source.source_id).encode() in exported_bytes
        assert str(exam.source.revision_id).encode() in exported_bytes
        assert repository.events.verify_projection(COURSE)

    with LocalRepository.open(root) as reopened:
        replayed = ExportService(reopened.events).assemble(COURSE, version=ExportVersion.V2)
        assert isinstance(replayed, ExportBundleV2)
        assert canonical_json_bytes(_bundle_json(replayed)) == exported_bytes
        assert reopened.events.verify_projection(COURSE)


def test_headless_surface_is_exactly_seven_public_tools() -> None:
    expected = (
        "citation.resolve",
        "course.get",
        "grounding.ask",
        "session.get_context",
        "session.record_note",
        "source.list",
        "source.search",
    )
    assert tuple(item.name for item in public_study_tool_manifests()) == expected
    assert tuple(
        str(cast(dict[str, object], item["manifest"])["name"])
        for item in public_study_tool_entries()
    ) == expected


def test_hybrid_dialogue_skip_is_bound_to_the_playbook_contract() -> None:
    # Suspension/resume behavior remains covered by
    # tests/integration/test_optional_dialogue_lifecycle.py; this story only
    # binds its ready-scope skip to the pinned playbook contract.
    dialogue_steps = tuple(
        step for step in HYBRID_FLASHCARDS_FLOW.steps if isinstance(step, DialogueStep)
    )
    assert len(dialogue_steps) == 1
    dialogue = dialogue_steps[0]
    assert dialogue.id == "clarify_hybrid_focus"
    assert dialogue.output_key == "clarification"
    assert dialogue.gate is not None
    assert dialogue.gate.default_response == {"provided": False, "text": ""}
    # The scripted public story is a ready scope, so the child observation is
    # terminal and does not manufacture a suspension/resume interaction.
    assert dialogue.gate.suspend_when.path == ("needs_clarification",)
