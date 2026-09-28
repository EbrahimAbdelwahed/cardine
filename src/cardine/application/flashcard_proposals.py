"""Canonical repository composition for lesson-scoped flashcard proposals."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable, Mapping
from hashlib import sha256
from typing import NoReturn, cast

from cardine.application.capability_completion import CapabilityCompletionProductReceipt
from cardine.application.flashcard_profile_selection import (
    FlashcardProfileRouteKind,
    FlashcardProfileSelectionDecision,
    select_flashcard_profile,
)
from cardine.hosts import TutorCapabilityCompletionReference
from study_agent.adapters.sqlite import NamespacedSQLiteRunStore, SQLiteRunStore
from study_agent.artifacts import ArtifactService, ArtifactSnapshot
from study_agent.artifacts.runtime import (
    VerifiedGeneratedBatchRuntime,
    compose_verified_generated_batch_runtime,
)
from study_agent.capabilities import (
    PROPOSE_FLASHCARDS_MANIFEST,
    CapabilityManifest,
    CapabilityOutcome,
    CompletedCapabilityOutcome,
    FailedCapabilityOutcome,
    TerminatedCapabilityOutcome,
)
from study_agent.capabilities.bindings import ProfiledCapabilityBinding
from study_agent.capabilities.builtin import explain_concept_binding
from study_agent.capabilities.fingerprints import capability_output_fingerprint
from study_agent.capabilities.gateway import StudyCapabilityGateway
from study_agent.capabilities.hybrid_flashcards import (
    HybridFlashcardTaskBinding,
    hybrid_flashcards_binding,
    hybrid_flashcards_validators,
)
from study_agent.capabilities.morphology_flashcards import (
    MorphologyFlashcardTaskBinding,
    morphology_flashcards_binding,
    morphology_flashcards_validators,
)
from study_agent.capabilities.worker_adapter import GatewayIsolatedCapabilityRunAdapter
from study_agent.domain import (
    Citation,
    CourseId,
    ExecutionContext,
    InteractionId,
    InteractionKind,
    ResolvedCitation,
    RevisionId,
    RunId,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject, JsonValue, freeze_object
from study_agent.flashcards.lesson_worker_contracts import (
    LessonWorkerCheckpoint,
    LessonWorkerRequest,
    LessonWorkerStatus,
    ProfileTaskExpectation,
    ResolvedPlannedBundleEvidence,
    RevisionContentCommitment,
    VerifiedFlashcardPageResult,
)
from study_agent.flashcards.lesson_worker_service import (
    LessonWorkerConflictError,
    LessonWorkerService,
)
from study_agent.flashcards.planning import (
    CanonicalSourceSpan,
    FlashcardLessonPlan,
    LessonGenerationUnit,
    LessonParagraph,
    LessonTopic,
    PlannedFlashcardBundle,
    PreparedPlannedFlashcardScope,
    plan_flashcard_lesson,
)
from study_agent.flashcards.worker_router import ClosedHistoricalPlannedBundleWorkerRouter
from study_agent.grounding import EvidenceEnvelope
from study_agent.pedagogy import (
    HYBRID_MACRO_DETAIL_V1,
    MORPHOLOGY_FIRST_ANATOMY_V1,
    PedagogicalProfileRef,
    ProfileSelectionReceipt,
)
from study_agent.playbooks import (
    PlaybookEngine,
    PlaybookRunStatus,
    PromptComposerRegistration,
    ReadDependency,
    RuntimeRegistries,
    ToolExecutor,
    ValidationOutcome,
    ValidatorDisposition,
    VerifiedRunRecord,
)
from study_agent.ports import (
    CancellationToken,
    ClockPort,
    EvidenceStatus,
    ModelCapabilities,
    ModelPort,
    ModelRequest,
    ModelResponse,
    ModelStreamEvent,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    SessionViewPort,
    SourceCommitmentLookupPort,
)
from study_agent.ports.lesson_worker import FlashcardProfileExecutionBinding
from study_agent.ports.retrieval import RetrievalDocument, retrieval_catalog_fingerprint
from study_agent.prompts import CanonicalPromptComposer
from study_agent.retrieval import (
    CourseSourceContent,
    SourceRevisionRecord,
    canonical_source_locator,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.state import canonical_json_bytes
from study_agent.tools.planned_flashcard_scope_bridge import planned_flashcard_scope_tool
from study_agent.workers import GenerationWorkerService
from study_agent.workers.contracts import GenerationWorkerTask
from study_agent.workers.view import WorkerCompactView, WorkerDetailView

_V1 = SemanticVersion.parse("1.0.0")
_CAPABILITY_IDENTITY = (
    f"{PROPOSE_FLASHCARDS_MANIFEST.id.value}@{PROPOSE_FLASHCARDS_MANIFEST.version.major}"
)


class _UnavailableExamScope:
    def prepare(self, request: object, context: ExecutionContext) -> NoReturn:
        del request, context
        raise RuntimeError("exam recovery is not part of flashcard composition")


class _UnavailableMedia:
    def resolve(self, handle: str) -> NoReturn:
        raise RuntimeError(f"verified media evidence is unavailable: {handle}")


class _UnavailableIsolatedRuns:
    async def start(self, task: object, context: ExecutionContext) -> NoReturn:
        del task, context
        raise RuntimeError("generation start is unavailable during detail recovery")

    async def resume(
        self, task: object, continuation: object, response: JsonValue, context: ExecutionContext
    ) -> NoReturn:
        del task, continuation, response, context
        raise RuntimeError("generation resume is unavailable during detail recovery")


class _ProfileGenerationModel:
    """Run the registered profile fallback validator for every structured draft."""

    def __init__(self, delegate: ModelPort) -> None:
        self._delegate = delegate

    @property
    def capabilities(self) -> ModelCapabilities:
        # Profile expectations intentionally include the structured-output
        # fallback validator. The provider may still receive the native schema;
        # this advertises only the verification mode used by this composition.
        return ModelCapabilities()

    async def generate(self, request: ModelRequest) -> ModelResponse:
        return await self._delegate.generate(request)

    def stream(self, request: ModelRequest) -> AsyncIterator[ModelStreamEvent]:
        return self._delegate.stream(request)

    async def cancel(self, token: CancellationToken) -> None:
        await self._delegate.cancel(token)


class _EnginePlannedFlashcardScopeTool:
    """Bind the existing versioned bridge to the playbook's artifact id."""

    name = "source.prepare_planned_flashcard_scope"

    def __init__(self, delegate: ToolExecutor) -> None:
        self._delegate = delegate
        self.behavior_version = delegate.behavior_version

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        return await self._delegate.invoke(arguments)


class _ProfileWorker:
    """Request-bound B1 worker that composes its exact scope bridge per child."""

    def __init__(
        self,
        composition: FlashcardProposalComposition,
        request: LessonWorkerRequest,
        task_binding: FlashcardProfileExecutionBinding,
        binding: ProfiledCapabilityBinding,
    ) -> None:
        self._composition = composition
        self._request = request
        self._task_binding = task_binding
        self._binding = binding

    @property
    def task_binding(self) -> FlashcardProfileExecutionBinding:
        return self._task_binding

    async def start(
        self,
        task: GenerationWorkerTask,
        prepared_scope: PreparedPlannedFlashcardScope,
        context: ExecutionContext,
    ) -> WorkerCompactView:
        expected = self._task_binding.build(
            task.task_id, self._request.to_public_inputs(), prepared_scope, context
        )
        if expected != task:
            raise RuntimeError("profiled lesson task changed")
        service = self._composition._generation_service(
            self._request, prepared_scope, self._binding, self._task_binding
        )
        view = await service.start(task, context)
        return view

    def detail(
        self,
        task_id: str,
        prepared_scope_fingerprint: str,
        context: ExecutionContext,
    ) -> VerifiedFlashcardPageResult:
        del prepared_scope_fingerprint
        service = GenerationWorkerService(
            store=self._composition._generation_store,
            isolated_runs=_UnavailableIsolatedRuns(),
        )
        return self._composition._page_result_from_detail(service.detail(task_id, context))


class _LessonEvidenceResolver:
    def __init__(
        self,
        content: CourseSourceContent,
        retired_source_ids: Callable[[], frozenset[SourceId]],
    ) -> None:
        self._content = content
        self._retired_source_ids = retired_source_ids
        self._documents_by_span: (
            dict[tuple[SourceId, RevisionId, int, int], RetrievalDocument] | None
        ) = None

    def resolve(
        self,
        plan: FlashcardLessonPlan,
        bundle: PlannedFlashcardBundle,
        revision_commitments: tuple[RevisionContentCommitment, ...],
        context: ExecutionContext,
    ) -> ResolvedPlannedBundleEvidence:
        del context
        evidence: list[RetrievalEvidence] = []
        if self._documents_by_span is None:
            documents = self._content.documents(include_superseded=True)
            indexed = {
                (
                    item.source_id,
                    item.revision_id,
                    item.chunk.start_offset,
                    item.chunk.end_offset,
                ): item
                for item in documents
            }
            if len(indexed) != len(documents):
                raise LessonWorkerConflictError("canonical source spans are not unique")
            self._documents_by_span = indexed
        for slot in bundle.slots:
            document = self._documents_by_span.get(
                (
                    slot.span.source_id,
                    slot.span.revision_id,
                    slot.span.start_offset,
                    slot.span.end_offset,
                )
            )
            if document is None:
                raise LessonWorkerConflictError("planned source chunk changed")
            if (
                document.source_id != slot.span.source_id
                or document.revision_id != slot.span.revision_id
                or document.chunk.start_offset != slot.span.start_offset
                or document.chunk.end_offset != slot.span.end_offset
            ):
                raise LessonWorkerConflictError("planned source span changed")
            text = self._content.get_text(document.revision_id)
            quoted = text[document.chunk.start_offset : document.chunk.end_offset]
            citation = self._content.resolve(
                Citation(
                    document.source_id,
                    document.revision_id,
                    document.chunk.chunk_id,
                    document.chunk.start_offset,
                    document.chunk.end_offset,
                    slot.span.locator,
                    quoted,
                )
            ).citation
            if citation.locator != slot.span.locator:
                raise LessonWorkerConflictError("canonical source locator changed")
            evidence.append(RetrievalEvidence(document.chunk, citation, quoted, 1.0))
        ordered = tuple(evidence)
        envelope = EvidenceEnvelope.from_retrieval(
            RetrievalEvidenceSet(
                EvidenceStatus.SUFFICIENT if ordered else EvidenceStatus.INSUFFICIENT,
                ordered,
                sha256(plan.plan_fingerprint.encode()).hexdigest(),
                "canonical-planned-lesson",
                "1.0.0",
                "event-source",
                _read_set_fingerprint(ordered),
            )
        )
        return ResolvedPlannedBundleEvidence(
            envelope,
            revision_commitments,
            plan.plan_fingerprint,
            bundle.bundle_id,
        )


class FlashcardProposalComposition:
    """One repository-owned executable graph for flashcard proposals."""

    def __init__(
        self,
        *,
        course_id: CourseId,
        session_id: SessionId,
        content: CourseSourceContent,
        course_profile: JsonObject,
        model: ModelPort,
        model_adapter: ArtifactReference,
        runs: SQLiteRunStore,
        clock: ClockPort,
        artifact_service: ArtifactService,
        source_commitments: SourceCommitmentLookupPort,
        sessions: SessionViewPort,
        interaction_id: InteractionId | None = None,
        retired_source_ids: Callable[[], frozenset[SourceId]] | frozenset[SourceId] = frozenset(),
    ) -> None:
        self._course_id = course_id
        self._session_id = session_id
        self._content = content
        self._course_profile = course_profile
        self._model = model
        self._model_adapter = model_adapter
        self._runs = runs
        self._clock = clock
        self._artifact_service = artifact_service
        self._source_commitments = source_commitments
        self._sessions = sessions
        self._interaction_id = interaction_id
        if callable(retired_source_ids):
            self._retired_source_ids = retired_source_ids
        else:
            retired = frozenset(retired_source_ids)
            self._retired_source_ids = lambda: retired
        self._lesson_store = _namespaced(runs, "lesson-worker")
        self._owner_store = _namespaced(runs, "generated-owner")
        self._generation_store = _namespaced(runs, "generation-worker")
        self._proof_store = _namespaced(runs, "verified-proof")
        self._course_profile_fingerprint = sha256(canonical_json_bytes(course_profile)).hexdigest()
        self._router = ClosedHistoricalPlannedBundleWorkerRouter(
            {
                HYBRID_MACRO_DETAIL_V1: self._worker_for_request,
                MORPHOLOGY_FIRST_ANATOMY_V1: self._worker_for_request,
            }
        )
        self._runtime: VerifiedGeneratedBatchRuntime = compose_verified_generated_batch_runtime(
            owner_store=self._owner_store,
            proof_store=self._proof_store,
            lesson_store=self._lesson_store,
            lesson_workers=self._router,
            exam_scope=_UnavailableExamScope(),
            source_content=content,
        )

    def _source_catalog_fingerprint(self) -> str:
        """Fingerprint only the active, non-retired current revision set."""

        active = tuple(
            document
            for document in self._content.documents()
            if document.source_id not in self._retired_source_ids()
        )
        return retrieval_catalog_fingerprint(active)

    @property
    def runtime(self) -> VerifiedGeneratedBatchRuntime:
        return self._runtime

    @property
    def manifest(self) -> CapabilityManifest:
        return PROPOSE_FLASHCARDS_MANIFEST

    def attach_artifact_service(self, artifact_service: ArtifactService) -> None:
        if not isinstance(artifact_service, ArtifactService):
            raise TypeError("flashcard composition requires ArtifactService")
        self._artifact_service = artifact_service

    def for_pin(
        self, pin: object, interaction_id: InteractionId | None = None
    ) -> FlashcardProposalComposition:
        """Return the same composition scoped to one complete lesson pin.

        The capability input schema remains the canonical harness schema.  A
        pin is therefore a Cardine composition concern: the derived content
        adapter below exposes only whole canonical chunks inside the selected
        span, while the worker, profile bindings, and generated-batch proof
        continue to use the unchanged Harness contracts.
        """

        from cardine.knowledge import SourcePin

        if not isinstance(pin, SourcePin):
            raise TypeError("flashcard lesson pin is invalid")
        scoped = _ScopedCourseSourceContent(self._content, pin)
        if not scoped.catalog():
            raise ValueError("lesson pin contains no complete canonical chunk")
        return FlashcardProposalComposition(
            course_id=self._course_id,
            session_id=self._session_id,
            content=cast(CourseSourceContent, scoped),
            course_profile=self._course_profile,
            model=self._model,
            model_adapter=self._model_adapter,
            runs=self._runs,
            clock=self._clock,
            artifact_service=self._artifact_service,
            source_commitments=self._source_commitments,
            sessions=self._sessions,
            interaction_id=interaction_id,
            retired_source_ids=self._retired_source_ids,
        )

    async def start_for_pin(
        self, inputs: JsonObject, pin: object, context: ExecutionContext
    ) -> CapabilityOutcome:
        """Generate only from a validated complete lesson pin."""

        return await self.for_pin(pin).start(inputs, context)

    async def start(self, inputs: JsonObject, context: ExecutionContext) -> CapabilityOutcome:
        public: JsonObject = inputs
        try:
            public = _public_inputs(inputs)
            prompt = str(public["query"])
            decision = select_flashcard_profile(prompt)
            if decision.kind is FlashcardProfileRouteKind.CLARIFICATION:
                raise ValueError(
                    decision.clarification or "flashcard profile selection is ambiguous"
                )
            request = self._request(public, context, decision)
            worker = self._worker_for_request(request)
            service = LessonWorkerService(
                store=self._lesson_store,
                resolver=_LessonEvidenceResolver(self._content, self._retired_source_ids),
                task_binding=worker.task_binding,
                worker=worker,
                owner_writer=self._runtime.lesson_owner_writer,
            )
            compact = await service.start(request, context)
            for _ in range(256):
                if not compact.advance_required:
                    break
                compact = await service.advance(compact.run_id, request, context)
        except (LessonWorkerConflictError, TypeError, ValueError, RuntimeError) as error:
            return FailedCapabilityOutcome(
                _fallback_run_id(public, context),
                "flashcard lesson generation could not be verified",
                _failure_reason(error),
            )
        except Exception as error:
            return FailedCapabilityOutcome(
                _fallback_run_id(public, context),
                "flashcard lesson generation could not be verified",
                _failure_reason(error),
            )
        if compact.status is LessonWorkerStatus.FAILED:
            return FailedCapabilityOutcome(
                compact.run_id,
                "flashcard lesson generation failed safely",
                _worker_failure_reason(compact.failure_codes),
            )
        if compact.status is not LessonWorkerStatus.COMPLETED:
            return FailedCapabilityOutcome(
                compact.run_id,
                "flashcard lesson generation did not reach a terminal state",
                "capability_execution_failed",
            )
        if compact.candidate_count < 1:
            return _terminated(
                compact.run_id,
                request,
                "insufficient verified source evidence for flashcard proposals",
            )
        output = _completion_output(request, compact.run_id, compact.candidate_count)
        return CompletedCapabilityOutcome(
            _verified_completion_run(request, compact.run_id, output), output
        )

    def recover(
        self,
        reference: TutorCapabilityCompletionReference,
        context: ExecutionContext | None = None,
    ) -> CapabilityCompletionProductReceipt | None:
        if (
            reference.capability_identity != _CAPABILITY_IDENTITY
            or reference.manifest_fingerprint != PROPOSE_FLASHCARDS_MANIFEST.fingerprint
            or context is None
        ):
            return None
        try:
            checkpoint = LessonWorkerCheckpoint.from_bytes(
                self._lesson_store.load(str(reference.run_id))
            )
            request = checkpoint.request
            worker = self._worker_for_request(request)
            service = LessonWorkerService(
                store=self._lesson_store,
                resolver=_LessonEvidenceResolver(self._content, self._retired_source_ids),
                task_binding=worker.task_binding,
                worker=worker,
                owner_writer=self._runtime.lesson_owner_writer,
            )
            reviewed = service.review_completed(reference.run_id, request, context)
            candidate_count = sum(len(page.batch.candidates) for page in reviewed.pages)
            if candidate_count < 1:
                return None
            output = _completion_output(request, reference.run_id, candidate_count)
            if capability_output_fingerprint(output) != reference.output_fingerprint:
                return None
            snapshot = self._artifact_service_snapshot(context)
            for page in reviewed.pages:
                page_checkpoint = checkpoint.pages[page.page_position]
                if page_checkpoint.receipt is None:
                    return None
                child_context = _proposal_context(
                    context, page_checkpoint.receipt.child_run_id, page.page_position
                )
                snapshot = self._artifact_service.record_generated(
                    page_checkpoint.receipt.child_run_id,
                    child_context,
                    snapshot.sequence,
                )
            ids = tuple(
                sorted(
                    {
                        str(request.profile_expectation.profile_selection_receipt.profile.id),
                        str(request.profile_expectation.profile_selection_receipt.profile.version),
                        *(str(item.revision_id) for item in request.revision_commitments),
                    }
                )
            )
            return CapabilityCompletionProductReceipt(
                _CAPABILITY_IDENTITY,
                reference.run_id,
                "Ho creato "
                f"{candidate_count} proposte flashcard in stato pending. "
                "Apri Proposte per rivederle.",
                ids,
            )
        except Exception:
            return None

    def _artifact_service_snapshot(self, context: ExecutionContext) -> ArtifactSnapshot:
        return self._artifact_service._view.get(context.course_id)

    def _latest_interaction_id(self) -> InteractionId:
        interactions = tuple(
            item
            for item in self._sessions.interactions(self._course_id, self._session_id)
            if item.kind is InteractionKind.HUMAN
        )
        if not interactions:
            raise ValueError(
                "explicit flashcard profile selection lacks learner interaction evidence"
            )
        return interactions[-1].id

    def _request(
        self,
        public: JsonObject,
        context: ExecutionContext,
        decision: FlashcardProfileSelectionDecision,
    ) -> LessonWorkerRequest:
        plan = _lesson_plan(self._content, self._retired_source_ids())
        interaction_id = self._interaction_id or self._latest_interaction_id()
        receipt = decision.receipt(interaction_id)
        profile = decision.profile
        if profile is None:
            raise ValueError("selected flashcard route has no profile")
        binding = _profile_binding(self, profile)
        expectation = _profile_expectation(binding, receipt)
        commitments = tuple(
            RevisionContentCommitment(record.source.revision_id, record.source.checksum_sha256)
            for record in self._content.catalog()
            if record.is_current_revision
            and record.source.source_id not in self._retired_source_ids()
        )
        return LessonWorkerRequest(
            plan,
            str(public["query"]),
            str(public["scope"] or str(public["query"])),
            str(public["language"]),
            _candidate_ceiling(public["candidate_ceiling"]),
            None,
            expectation,
            8,
            commitments,
        )

    def _worker_for_request(self, request: LessonWorkerRequest) -> _ProfileWorker:
        profile = request.profile_expectation.profile_selection_receipt.profile
        binding = _profile_binding(self, profile)
        if profile == HYBRID_MACRO_DETAIL_V1:
            task_binding: FlashcardProfileExecutionBinding = HybridFlashcardTaskBinding(
                request, binding
            )
        elif profile == MORPHOLOGY_FIRST_ANATOMY_V1:
            task_binding = MorphologyFlashcardTaskBinding(request, binding)
        else:  # pragma: no cover - closed by ProfileSelectionReceipt
            raise ValueError("unsupported flashcard profile")
        return _ProfileWorker(self, request, task_binding, binding)

    def _generation_service(
        self,
        request: LessonWorkerRequest,
        prepared_scope: PreparedPlannedFlashcardScope,
        binding: ProfiledCapabilityBinding,
        task_binding: FlashcardProfileExecutionBinding,
    ) -> GenerationWorkerService:
        tool = _EnginePlannedFlashcardScopeTool(
            planned_flashcard_scope_tool(request, prepared_scope)
        )
        validators = (
            hybrid_flashcards_validators(self._content)
            if binding.profile == HYBRID_MACRO_DETAIL_V1
            else morphology_flashcards_validators(self._content, _UnavailableMedia())
        )
        engine = PlaybookEngine(
            engine_version=_V1,
            model_adapter=self._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
            model=_ProfileGenerationModel(self._model),
            registries=RuntimeRegistries(
                (tool,),
                validators,
                (
                    PromptComposerRegistration(
                        binding.pins.prompt,
                        CanonicalPromptComposer(allow_profile_layers=True),
                    ),
                ),
            ),
            run_store=self._runs,
            clock=self._clock,
        )
        base = explain_concept_binding(
            dependency_resolver=lambda *, context, inputs: (
                ReadDependency(
                    "course_profile", str(context.course_id), self._course_profile_fingerprint
                ),
            ),
            model_adapter=self._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
        )
        gateway = StudyCapabilityGateway(bindings=(base,), engine=engine)
        descriptor = task_binding.execution_descriptor
        adapter = GatewayIsolatedCapabilityRunAdapter(
            gateway=gateway,
            bindings=(descriptor,),
            proof_owner=self._runtime.proofs,
        )
        return GenerationWorkerService(store=self._generation_store, isolated_runs=adapter)

    def _page_result_from_detail(self, detail: WorkerDetailView) -> VerifiedFlashcardPageResult:
        from study_agent.artifacts.candidates import FlashcardCandidateBatch

        if not isinstance(detail.output, Mapping):
            raise RuntimeError("verified flashcard output is not an object")
        batch = FlashcardCandidateBatch.from_json(detail.output)
        return VerifiedFlashcardPageResult(
            len(batch.candidates), len(batch.omissions), detail.receipt.output_fingerprint, detail
        )


def _namespaced(runs: SQLiteRunStore, namespace: str) -> NamespacedSQLiteRunStore:
    return NamespacedSQLiteRunStore(runs, namespace)


def _profile_binding(
    composition: FlashcardProposalComposition, profile: PedagogicalProfileRef
) -> ProfiledCapabilityBinding:
    def resolver(*, context: ExecutionContext, inputs: JsonObject) -> tuple[ReadDependency, ...]:
        del inputs
        dependencies: list[ReadDependency] = [
            ReadDependency(
                "course_profile",
                str(context.course_id),
                composition._course_profile_fingerprint,
            ),
            ReadDependency(
                "source_revision_set",
                str(context.course_id),
                composition._source_catalog_fingerprint(),
            ),
        ]
        dependencies.extend(
            ReadDependency(
                "source_revision",
                str(record.source.source_id),
                str(record.source.revision_id),
            )
            for record in composition._content.catalog()
            if record.is_current_revision
            and record.source.source_id not in composition._retired_source_ids()
        )
        return tuple(dependencies)

    if profile == HYBRID_MACRO_DETAIL_V1:
        return hybrid_flashcards_binding(
            dependency_resolver=resolver,
            model_adapter=composition._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
        )
    if profile == MORPHOLOGY_FIRST_ANATOMY_V1:
        return morphology_flashcards_binding(
            dependency_resolver=resolver,
            model_adapter=composition._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
        )
    raise ValueError("flashcard profile is not registered")


def _profile_expectation(
    binding: ProfiledCapabilityBinding, receipt: ProfileSelectionReceipt
) -> ProfileTaskExpectation:
    from study_agent.capabilities.hybrid_flashcards import (
        _validation_expectations as hybrid_validations,
    )
    from study_agent.capabilities.morphology_flashcards import (
        _validation_expectations as morphology_validations,
    )
    from study_agent.playbooks import playbook_definition_fingerprint
    from study_agent.workers.contracts import fingerprint_output_schema

    validations = (
        hybrid_validations()
        if binding.profile == HYBRID_MACRO_DETAIL_V1
        else morphology_validations()
    )
    return ProfileTaskExpectation(
        receipt,
        binding.manifest.id,
        binding.manifest.version,
        binding.manifest_fingerprint,
        binding.manifest.required_authority,
        binding.pins,
        playbook_definition_fingerprint(binding.playbook),
        binding.manifest.output_schema,
        fingerprint_output_schema(binding.manifest.output_schema),
        validations,
    )


def _lesson_plan(
    content: CourseSourceContent, retired_source_ids: frozenset[SourceId] = frozenset()
) -> FlashcardLessonPlan:
    records = tuple(
        record
        for record in content.catalog()
        if record.is_current_revision and record.source.source_id not in retired_source_ids
    )
    topics: list[LessonTopic] = []
    paragraphs: list[LessonParagraph] = []
    position = 0
    for record in records:
        for chunk in record.chunks:
            section = " > ".join(chunk.section_path) or f"chunk {chunk.ordinal + 1}"
            span = CanonicalSourceSpan(
                chunk.source_id,
                chunk.revision_id,
                chunk.start_offset,
                chunk.end_offset,
                canonical_source_locator(record, chunk, chunk.start_offset, chunk.end_offset),
            )
            topic_key = f"topic-{position:04d}"
            paragraph_key = f"paragraph-{position:04d}"
            topics.append(
                LessonTopic(
                    topic_key,
                    f"{record.source.title} — {section}",
                    1,
                    None,
                    position,
                    span,
                    (paragraph_key,),
                )
            )
            paragraphs.append(
                LessonParagraph(
                    paragraph_key,
                    topic_key,
                    position,
                    span,
                    chunk.end_offset - chunk.start_offset,
                )
            )
            position += 1
    return plan_flashcard_lesson(
        LessonGenerationUnit(
            "course-current-sources",
            records[0].source.title if len(records) == 1 else "Current course sources",
            tuple(topics),
            tuple(paragraphs),
        )
    )


def _candidate_ceiling(value: JsonValue) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("candidate ceiling is invalid")
    return value


def _public_inputs(inputs: JsonObject) -> JsonObject:
    expected = {"query", "scope", "language", "candidate_ceiling", "continuation_summary_json"}
    if set(inputs) != expected:
        raise ValueError("flashcard capability inputs are not exact")
    if not isinstance(inputs["query"], str) or not inputs["query"].strip():
        raise ValueError("flashcard query is invalid")
    if inputs["scope"] is not None and (
        not isinstance(inputs["scope"], str) or not inputs["scope"].strip()
    ):
        raise ValueError("flashcard scope is invalid")
    return freeze_object(inputs)


def _completion_output(
    request: LessonWorkerRequest, run_id: RunId, candidate_count: int
) -> JsonObject:
    return freeze_object(
        {
            "status": "proposed",
            "lesson_run_id": str(run_id),
            "candidate_count": candidate_count,
            "profile": request.profile_expectation.profile_selection_receipt.profile.identity,
            "profile_expectation_fingerprint": request.profile_expectation.fingerprint,
        }
    )


def _verified_completion_run(
    request: LessonWorkerRequest, run_id: RunId, output: JsonObject
) -> VerifiedRunRecord:
    return VerifiedRunRecord(
        run_id,
        request.profile_expectation.definition_fingerprint,
        request.to_public_inputs(),
        request.profile_expectation.pins,
        (),
        output,
        (),
        PlaybookRunStatus.COMPLETED,
    )


def _terminated(
    run_id: RunId, request: LessonWorkerRequest, message: str
) -> TerminatedCapabilityOutcome:
    return TerminatedCapabilityOutcome(
        VerifiedRunRecord(
            run_id,
            request.profile_expectation.definition_fingerprint,
            request.to_public_inputs(),
            request.profile_expectation.pins,
            (),
            {},
            (),
            PlaybookRunStatus.TERMINATED,
            ValidationOutcome(True, ValidatorDisposition.TERMINATE, {"message": message}, message),
        )
    )


def _proposal_context(
    context: ExecutionContext, child_run_id: RunId, position: int
) -> ExecutionContext:
    from dataclasses import replace

    return replace(
        context,
        idempotency_key=(
            f"{context.idempotency_key or 'flashcard'}:proposal:{position}:{child_run_id}"
        ),
    )


def _fallback_run_id(inputs: JsonObject, context: ExecutionContext) -> RunId:
    digest = sha256(
        canonical_json_bytes(
            {"course": str(context.course_id), "session": str(context.session_id), "inputs": inputs}
        )
    ).hexdigest()
    return RunId(f"flashcard-fallback-sha256:{digest}")


def _worker_failure_reason(failure_codes: tuple[str, ...]) -> str:
    codes = frozenset(failure_codes)
    exact = {
        "gateway_authentication": "authentication",
        "gateway_authorization": "authorization",
        "gateway_model_unavailable": "model_unavailable",
        "gateway_endpoint_incompatible": "endpoint_incompatible",
        "gateway_schema_incompatible": "schema_incompatible",
        "gateway_rate_limited": "rate_limited",
        "gateway_timeout": "timeout",
        "gateway_protocol_error": "protocol_error",
        "gateway_unavailable": "unavailable",
    }
    for code, reason in exact.items():
        if code in codes:
            return reason
    if any("stale" in code or "scope" in code for code in codes):
        return "scope_stale"
    if any("validation" in code or "proof" in code for code in codes):
        return "capability_validation_failed"
    return "capability_execution_failed"


def _failure_reason(error: Exception) -> str:
    if isinstance(error, LessonWorkerConflictError):
        return "scope_stale"
    return "capability_execution_failed"


def _read_set_fingerprint(evidence: tuple[RetrievalEvidence, ...]) -> str:
    from study_agent.ports import retrieval_read_set_fingerprint

    return retrieval_read_set_fingerprint(evidence)


class _ScopedCourseSourceContent:
    """Read-only canonical content view limited to one SourcePin span."""

    def __init__(self, parent: CourseSourceContent, pin: object) -> None:
        from cardine.knowledge import SourcePin

        if not isinstance(pin, SourcePin):
            raise TypeError("flashcard lesson pin is invalid")
        self._parent = parent
        self._pin = pin

    def catalog(self) -> tuple[SourceRevisionRecord, ...]:
        records: list[SourceRevisionRecord] = []
        for record in self._parent.catalog():
            if (
                str(record.source.source_id) != self._pin.source_id
                or str(record.source.revision_id) != self._pin.revision_id
                or not record.is_current_revision
            ):
                continue
            chunks = tuple(
                chunk
                for chunk in record.chunks
                if chunk.start_offset >= self._pin.start_offset
                and chunk.end_offset <= self._pin.end_offset
            )
            if chunks:
                records.append(
                    SourceRevisionRecord(
                        record.course_id,
                        record.source,
                        chunks,
                        _mask_outside_pin(
                            record.text, self._pin.start_offset, self._pin.end_offset
                        ),
                        record.is_current_revision,
                    )
                )
        return tuple(records)

    def documents(self, *, include_superseded: bool = False) -> tuple[RetrievalDocument, ...]:
        del include_superseded
        documents = []
        for record in self.catalog():
            for chunk in record.chunks:
                documents.append(
                    RetrievalDocument(
                        record.course_id,
                        record.source.source_id,
                        record.source.revision_id,
                        chunk,
                        record.text[chunk.start_offset : chunk.end_offset],
                        record.source.title,
                        record.source.kind,
                        record.source.source_role,
                        record.source.trust_level,
                        record.is_current_revision,
                    )
                )
        return tuple(documents)

    def get_text(self, revision_id: RevisionId) -> str:
        if str(revision_id) != self._pin.revision_id:
            raise LookupError("lesson pin revision is outside the selected scope")
        return _mask_outside_pin(
            self._parent.get_text(RevisionId(self._pin.revision_id)),
            self._pin.start_offset,
            self._pin.end_offset,
        )

    def resolve(self, citation: Citation) -> ResolvedCitation:
        if (
            str(citation.source_id) != self._pin.source_id
            or str(citation.revision_id) != self._pin.revision_id
            or citation.start_offset < self._pin.start_offset
            or citation.end_offset > self._pin.end_offset
        ):
            raise ValueError("citation lies outside the selected lesson pin")
        return self._parent.resolve(citation)

    def canonical_document(self, chunk_id: object) -> RetrievalDocument:
        from study_agent.domain import ChunkId

        if not isinstance(chunk_id, ChunkId):
            raise TypeError("canonical chunk id is invalid")
        if all(document.chunk.chunk_id != chunk_id for document in self.documents()):
            raise LookupError("canonical chunk lies outside the selected lesson pin")
        return self._parent.canonical_document(chunk_id)


def _mask_outside_pin(text: str, start_offset: int, end_offset: int) -> str:
    """Preserve canonical offsets while making unselected text unreadable."""

    start = max(0, min(start_offset, len(text)))
    end = max(start, min(end_offset, len(text)))
    return " " * start + text[start:end] + " " * (len(text) - end)


__all__ = ["FlashcardProposalComposition"]
