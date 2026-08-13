"""Auditable composition root for one local study-agent repository."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from cardine.adapters.model.retrieval_query_recovery import RetrievalQueryRecovery
from cardine.adapters.pageindex import PageIndexCoordinator, PageIndexRevision
from cardine.application.flashcard_proposals import FlashcardProposalComposition
from cardine.application.indexing import (
    IndexingCoordinator,
    IndexingPhase,
    IndexingRecord,
    IndexingStatus,
)
from cardine.courses import (
    CourseService,
    ProjectionCourseCatalog,
    ProjectionCourseView,
    course_profile_manifest,
    register_course_events,
)
from cardine.hosts import (
    HostActionIdentity,
    SourceGroundedTutorDecisionPort,
    TutorCapabilityCompletionReference,
    TutorHostContextAssembler,
    TutorHostLimits,
    TutorHostRunner,
    TutorHostRunStatus,
)
from cardine.hosts.flashcard_routing import FlashcardProfileRoutingTutorDecisionPort
from cardine.integrations.study_agent.course_policy import (
    ConsentModelPort,
    CourseConsentService,
    ProjectionConsentView,
    ProjectionSourceLifetimeView,
    ProviderConsentRequiredError,
    SourceLifetimeService,
    register_course_policy_events,
)
from cardine.knowledge import (
    LessonCandidate,
    LessonChunk,
    LessonEvidencePort,
    LessonSearchResult,
    LessonSelectionError,
    LessonSelectionService,
    LessonSource,
    PageIndexProjection,
    PageIndexStatus,
    SearchDisposition,
    SourcePin,
    lesson_title_matches,
)
from study_agent.adapters.filesystem import (
    BlobIntegrityError,
    BlobNotFoundError,
    FilesystemBlobStore,
    LocalRepositoryError,
    LocalRepositoryPaths,
    UnsafeBlobPathError,
    initialize_local_repository,
    validate_local_repository_layout,
)
from study_agent.adapters.filesystem.repository_target import RepositoryObservationHandle
from study_agent.adapters.model import (
    ADAPTER_ID as OPENAI_COMPATIBLE_ADAPTER_ID,
)
from study_agent.adapters.model import (
    ADAPTER_VERSION as OPENAI_COMPATIBLE_ADAPTER_VERSION,
)
from study_agent.adapters.model import (
    GPT_5_6_LUNA_ADAPTER_ID,
    GPT_5_6_LUNA_ADAPTER_VERSION,
    ModelTutorDecisionPort,
    OpenAICompatibleConfig,
    OpenAICompatibleModel,
    OpenAIGpt56LunaConfig,
    OpenAIGpt56LunaModel,
)
from study_agent.adapters.sqlite import (
    NamespacedSQLiteRunStore,
    SQLiteConnectionIdentityGuard,
    SQLiteEventStore,
    SQLiteFtsRetrieval,
    SQLiteRunStore,
    SQLiteTutorContinuationStore,
)
from study_agent.adapters.system import SystemClock
from study_agent.application import (
    CapabilityCompletionHandler,
    CapabilityCompletionHandlerRegistry,
    CapabilityCompletionProductReceipt,
    ConversationTurnApplication,
    GroundingAskConfiguration,
    GroundingAskError,
    GroundingAskErrorCode,
    GroundingAskService,
    GroundingEngineFactory,
    StudyReadinessView,
)
from study_agent.artifacts import (
    ArtifactService,
    ProjectionArtifactView,
    register_artifact_events,
)
from study_agent.assessments import (
    AssessmentService,
    ExactClosedGradingPolicy,
    ProjectionAssessmentView,
    ProjectionLearnerEvidenceView,
    register_assessment_events,
)
from study_agent.capabilities import (
    EXPLAIN_CONCEPT_MANIFEST,
    CapabilityContinuation,
    CapabilityManifest,
    CapabilityOutcome,
    CompletedCapabilityOutcome,
    StudyCapabilityGateway,
    TutorCapabilityId,
    builtin_tutor_validators,
    explain_concept_binding,
)
from study_agent.capabilities.fingerprints import (
    capability_output_fingerprint,
    capability_retry_fingerprint,
)
from study_agent.domain import (
    BlobId,
    BlobRef,
    ChunkId,
    Citation,
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    ResolvedCitation,
    RevisionId,
    SessionId,
    SessionStatus,
    SourceCommitment,
    SourceId,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.grounding import (
    EvidenceSufficiencyValidator,
    GroundedAnswerIntegrityValidator,
)
from study_agent.ingestion import TextIngestionService, register_source_revision_events
from study_agent.playbooks import (
    PlaybookEngine,
    PromptComposerRegistration,
    ReadDependency,
    RuntimeRegistries,
    ToolBehaviorPin,
    ToolExecutor,
    VersionPins,
)
from study_agent.playbooks.builtin import GROUNDED_ANSWER_FLOW
from study_agent.ports import IndexReceipt, ModelCapabilities, ModelPort
from study_agent.ports.retrieval import (
    EvidenceStatus,
    RetrievalDocument,
    RetrievalEvidence,
    RetrievalEvidenceSet,
    RetrievalPort,
    RetrievalQuery,
    retrieval_catalog_fingerprint,
    retrieval_read_set_fingerprint,
)
from study_agent.ports.scheduling import SchedulingPolicyPort
from study_agent.ports.tutor_runner import (
    TutorCompletionHandoffStore,
    TutorContinuationStore,
)
from study_agent.prompts import GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer
from study_agent.recall import register_recall_events
from study_agent.recall.composition import (
    RecallAvailability,
    RecallComposition,
    compose_recall,
)
from study_agent.repository_config import LocalRepositoryConfig, ModelAdapterConfig
from study_agent.retrieval import (
    CourseSourceContent,
    SourceContentError,
    SourceContentErrorCode,
)
from study_agent.sessions import (
    GroundedSessionFinalizer,
    ProjectionAssistantTurnView,
    ProjectionSessionView,
    ProjectionTutorPresentationView,
    SessionService,
    SessionTurnService,
    register_session_events,
)
from study_agent.skills import ArtifactReference, SemanticVersion
from study_agent.skills.builtin import GROUNDED_ANSWER_SKILL
from study_agent.state import EventRegistry, canonical_json_bytes
from study_agent.study_context import (
    ProjectionStudyContextView,
    StudyContextService,
    register_study_context_events,
)
from study_agent.tools import BoundSourceSearchExecutor
from study_agent.tutor_snapshot import TutorSnapshotReader

if TYPE_CHECKING:
    from cardine.hosts.context import HarnessToolManifestView
    from study_agent.application import HarnessToolSurface
    from study_agent.artifacts.contracts import (
        ServiceDecisionPolicyReceipt,
        ServiceDecisionPolicyRequest,
        VerifiedGeneratedArtifactBatch,
    )
    from study_agent.domain import RunId
    from study_agent.tools import StudyToolRegistry

_V1 = SemanticVersion.parse("1.0.0")

_INSUFFICIENT_EVIDENCE_MESSAGE = (
    "Non ho trovato evidenze sufficienti nei materiali disponibili per rispondere. "
    "Prova a indicare una fonte o a riformulare la richiesta."
)
_GENERIC_TUTOR_FAILURE_MESSAGE = (
    "Non sono riuscito a completare questa risposta. "
    "Riprova tra poco oppure riformula la richiesta."
)

_PAGEINDEX_RECONCILE_BUDGET = 32
_PAGEINDEX_ADMISSION_BUDGET = 4
_LESSON_SEARCH_SOURCE_BUDGET = 32


class _PinnedRetrieval:
    """Restrict canonical retrieval evidence to one validated lesson pin."""

    def __init__(self, inner: RetrievalPort, pin: SourcePin) -> None:
        self._inner = inner
        self._pin = pin

    def index(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt:
        return self._inner.index(documents)

    def search(self, query: RetrievalQuery) -> RetrievalEvidenceSet:
        scoped = replace(query, revision_ids=(RevisionId(self._pin.revision_id),))
        evidence = self._inner.search(scoped)
        selected = tuple(
            item
            for item in evidence.evidence
            if str(item.citation.source_id) == self._pin.source_id
            and str(item.citation.revision_id) == self._pin.revision_id
            and item.citation.start_offset >= self._pin.start_offset
            and item.citation.end_offset <= self._pin.end_offset
        )
        status = evidence.status if selected else EvidenceStatus.INSUFFICIENT
        return RetrievalEvidenceSet(
            status,
            selected,
            evidence.query_fingerprint,
            evidence.strategy_id,
            evidence.strategy_version,
            evidence.index_version,
            retrieval_read_set_fingerprint(selected),
        )


class _QueryOverrideRetrieval:
    """Use one recovered lexical query without changing capability input identity."""

    def __init__(self, inner: RetrievalPort, original: str, recovered: str) -> None:
        self._inner = inner
        self._original = original
        self._recovered = recovered

    def index(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt:
        return self._inner.index(documents)

    def search(self, query: RetrievalQuery) -> RetrievalEvidenceSet:
        effective = (
            replace(query, text=self._recovered)
            if query.text == self._original
            else query
        )
        return self._inner.search(effective)


class _StructuralRangeRetrieval:
    """Return all complete canonical chunks inside one validated structural pin."""

    def __init__(
        self,
        catalog: CourseSourceContent,
        pin: SourcePin,
        index_receipt: IndexReceipt,
    ) -> None:
        self._catalog = catalog
        self._pin = pin
        self._index_receipt = index_receipt

    def index(self, documents: Sequence[RetrievalDocument]) -> IndexReceipt:
        del documents
        raise PermissionError("structural range retrieval is read-only")

    def search(self, query: RetrievalQuery) -> RetrievalEvidenceSet:
        documents = tuple(
            document
            for document in self._catalog.documents()
            if str(document.source_id) == self._pin.source_id
            and str(document.revision_id) == self._pin.revision_id
            and document.chunk.start_offset >= self._pin.start_offset
            and document.chunk.end_offset <= self._pin.end_offset
            and document.trust_level >= query.minimum_trust_level
            and (not query.source_kinds or document.source_kind in query.source_kinds)
            and (not query.source_roles or document.source_role in query.source_roles)
        )
        if len(documents) > 100:
            raise ValueError("lesson scope exceeds the canonical evidence bound")
        evidence_rows: list[RetrievalEvidence] = []
        for document in sorted(documents, key=lambda item: item.chunk.ordinal):
            resolved = self._catalog.resolve(
                Citation(
                    document.source_id,
                    document.revision_id,
                    document.chunk.chunk_id,
                    document.chunk.start_offset,
                    document.chunk.end_offset,
                    "cardine-structural-range",
                    document.text,
                )
            )
            evidence_rows.append(
                RetrievalEvidence(document.chunk, resolved.citation, resolved.text, 1.0)
            )
        evidence = tuple(evidence_rows)
        fingerprint = sha256(
            b"cardine-structural-query@1\0" + canonical_json_bytes(
                {
                    "course_id": str(query.course_id),
                    "text": query.text,
                    "pin": {
                        "source_id": self._pin.source_id,
                        "revision_id": self._pin.revision_id,
                        "start_offset": self._pin.start_offset,
                        "end_offset": self._pin.end_offset,
                    },
                }
            )
        ).hexdigest()
        return RetrievalEvidenceSet(
            EvidenceStatus.SUFFICIENT if evidence else EvidenceStatus.INSUFFICIENT,
            evidence,
            fingerprint,
            "cardine-structural-range",
            "1.0.0",
            self._index_receipt.index_version,
            retrieval_read_set_fingerprint(evidence),
        )


def _cardine_fallback_message(status: TutorHostRunStatus, failure_reason: str | None) -> str:
    """Return localized learner-safe copy without exposing operational details."""

    del failure_reason
    if status in {TutorHostRunStatus.TERMINATED, TutorHostRunStatus.STOPPED}:
        return _INSUFFICIENT_EVIDENCE_MESSAGE
    return _GENERIC_TUTOR_FAILURE_MESSAGE


class _RepositoryTutorGateway:
    """Request-bound real explain capability over canonical repository reads."""

    def __init__(
        self,
        repository: LocalRepository,
        course_id: CourseId,
        session_id: SessionId,
        model: ModelPort,
        model_adapter: ArtifactReference,
        flashcards: FlashcardProposalComposition | None = None,
        lesson_pin: SourcePin | None = None,
    ) -> None:
        self._repository = repository
        self._course_id = course_id
        self._session_id = session_id
        self._model = model
        self._model_adapter = model_adapter
        self._flashcards = flashcards
        self._lesson_pin = lesson_pin

    def recover(
        self,
        reference: TutorCapabilityCompletionReference,
        context: ExecutionContext | None = None,
    ) -> CapabilityCompletionProductReceipt | None:
        """Recover one verified explain output without invoking the provider."""

        if context is None:
            return None
        if self._flashcards is not None:
            recovered = self._flashcards.recover(reference, context)
            if recovered is not None:
                return recovered
        if (
            reference.capability_identity
            != (f"{EXPLAIN_CONCEPT_MANIFEST.id.value}@{EXPLAIN_CONCEPT_MANIFEST.version.major}")
            or reference.manifest_fingerprint != EXPLAIN_CONCEPT_MANIFEST.fingerprint
        ):
            return None
        try:
            gateway = self._gateway({"query": "completion recovery"}, context)
            output = gateway.recover_completed(
                reference.capability_identity,
                reference.manifest_fingerprint,
                reference.run_id,
                reference.output_fingerprint,
                reference.retry_receipt_fingerprint,
                context,
            )
            if output is None:
                return None
            return _explanation_product_receipt(reference, output)
        except Exception:
            return None

    def discover(self) -> tuple[CapabilityManifest, ...]:
        manifests = [EXPLAIN_CONCEPT_MANIFEST]
        if self._flashcards is not None:
            manifests.append(self._flashcards.manifest)
        return tuple(manifests)

    async def start(
        self,
        capability_id: TutorCapabilityId,
        inputs: JsonObject,
        context: ExecutionContext,
    ) -> CapabilityOutcome:
        self._require_provider_consent()
        if capability_id is TutorCapabilityId.PROPOSE_FLASHCARDS:
            if self._flashcards is None:
                raise ValueError("flashcard capability is not executable")
            return await self._flashcards.start(inputs, context)
        gateway = self._gateway(inputs, context)
        recovered_query = await self._recover_empty_retrieval_query(inputs)
        if recovered_query is not None:
            gateway = self._gateway(inputs, context, recovered_query=recovered_query)
        outcome = await gateway.start(capability_id, inputs, context)
        return outcome

    async def resume(
        self,
        continuation: CapabilityContinuation,
        response: JsonValue,
        context: ExecutionContext,
    ) -> CapabilityOutcome:
        self._require_provider_consent()
        if continuation.capability_id is TutorCapabilityId.PROPOSE_FLASHCARDS:
            raise ValueError("flashcard lesson workers do not expose dialogue continuation")
        inputs = getattr(continuation, "inputs", None)
        if not isinstance(inputs, Mapping):
            raise TypeError("continuation inputs are invalid")
        outcome = await self._gateway(inputs, context).resume(continuation, response, context)
        return outcome

    def _require_provider_consent(self) -> None:
        receipt = self._repository.provider_consent.get(self._course_id)
        if receipt is None or not receipt.granted:
            raise ProviderConsentRequiredError("provider consent is required")

    async def _recover_empty_retrieval_query(
        self, inputs: JsonObject
    ) -> str | None:
        """Recover one unpinned empty FTS query through a bounded Luna call."""

        query = inputs.get("query")
        if not isinstance(query, str) or not query.strip():
            return None
        if self._lesson_pin is not None:
            return None
        if self._repository.resolve_lesson_scope(self._course_id, query) is not None:
            return None

        course = self._repository.for_course(self._course_id)
        profile = course_profile_manifest(self._repository.courses.get(self._course_id))
        policy = profile.get("source_policy")
        if not isinstance(policy, Mapping):
            return None
        minimum = policy.get("minimum_trust_level")
        roles = policy.get("allowed_roles")
        if not isinstance(minimum, int) or isinstance(minimum, bool):
            return None
        if not isinstance(roles, tuple) or any(not isinstance(item, str) for item in roles):
            return None
        retrieval_query = RetrievalQuery(
            self._course_id,
            query,
            limit=8,
            minimum_trust_level=minimum,
            source_roles=tuple(cast(str, item) for item in roles),
        )
        if course.retrieval.search(retrieval_query).status is not EvidenceStatus.INSUFFICIENT:
            return None

        vocabulary: list[str] = []
        seen: set[str] = set()
        retired = self._repository.source_lifetime.retired_source_ids(self._course_id)
        allowed_roles = frozenset(cast(str, item) for item in roles)
        for document in course.content.documents():
            if (
                not document.is_current_revision
                or document.source_id in retired
                or document.trust_level < minimum
                or (allowed_roles and document.source_role not in allowed_roles)
            ):
                continue
            for value in (document.title, *document.chunk.section_path):
                candidate = " ".join(value.split())[:160]
                folded = candidate.casefold()
                if candidate and folded not in seen:
                    seen.add(folded)
                    vocabulary.append(candidate)
                if len(vocabulary) >= 64:
                    break
            if len(vocabulary) >= 64:
                break
        target = inputs.get("target")
        learner_request = target if isinstance(target, str) and target.strip() else query
        alternatives = await RetrievalQueryRecovery(self._model).alternatives(
            learner_request=learner_request,
            failed_query=query,
            source_vocabulary=vocabulary,
        )
        for alternative in alternatives:
            candidate_query = replace(retrieval_query, text=alternative)
            if (
                course.retrieval.search(candidate_query).status
                is not EvidenceStatus.INSUFFICIENT
            ):
                return alternative
        return None

    def _gateway(
        self,
        inputs: Mapping[str, object],
        context: ExecutionContext,
        *,
        recovered_query: str | None = None,
    ) -> StudyCapabilityGateway:
        query = inputs.get("query")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("explain capability query is invalid")
        repository = self._repository
        course = repository.for_course(self._course_id)
        profile = course_profile_manifest(repository.courses.get(self._course_id))
        current_target = retrieval_catalog_fingerprint(
            tuple(repository._source_catalog.documents(include_superseded=True))
        )
        indexing = repository.indexing_status()
        if indexing is None or indexing.target_fingerprint != current_target:
            # One-time compatibility migration for repositories created before
            # durable derived-index status existed, and direct non-UI source
            # mutations. New browser admissions queue explicitly and reconcile
            # outside the upload request.
            repository.reconcile_indexing()
        receipt = course.retrieval.audit()
        course_receipt = repository.course_index_receipt(self._course_id, receipt)
        # A lesson the learner attached to the chat is an explicit decision and
        # outranks any lesson reference inferred from the wording of the turn.
        lesson_pin = self._lesson_pin
        if lesson_pin is None:
            lesson_pin = repository.resolve_lesson_scope(self._course_id, query)
        if context.session_id is None:
            raise ValueError("explain capability requires a session")
        profile_fingerprint = sha256(canonical_json_bytes(profile)).hexdigest()
        retrieval_fingerprint = sha256(
            (
                f"{course_receipt.indexed_chunks}\0"
                f"{course_receipt.index_version}\0"
                f"{course_receipt.catalog_fingerprint}"
            ).encode()
        ).hexdigest()

        def dependencies(
            *, context: ExecutionContext, inputs: JsonObject
        ) -> tuple[ReadDependency, ...]:
            del inputs
            return (
                ReadDependency(
                    "course_profile",
                    str(context.course_id),
                    profile_fingerprint,
                ),
                ReadDependency(
                    "source_revision_set",
                    str(context.course_id),
                    course_receipt.catalog_fingerprint,
                ),
                ReadDependency(
                    "retrieval_index",
                    str(context.course_id),
                    retrieval_fingerprint,
                ),
            )

        binding = explain_concept_binding(
            dependency_resolver=dependencies,
            model_adapter=self._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
        )
        retrieval: RetrievalPort = course.retrieval
        retrieval_limit = 8
        if lesson_pin is not None:
            repository.validate_lesson_pin(lesson_pin)
            retrieval = _StructuralRangeRetrieval(course.content, lesson_pin, course_receipt)
            retrieval_limit = 100
        elif recovered_query is not None:
            retrieval = _QueryOverrideRetrieval(retrieval, query, recovered_query)
        search = BoundSourceSearchExecutor(
            context=context,
            question=query,
            retrieval=retrieval,
            course_profile=profile,
            index_receipt=course_receipt,
            limit=retrieval_limit,
        )
        engine = PlaybookEngine(
            engine_version=_V1,
            model_adapter=self._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
            model=self._model,
            registries=RuntimeRegistries(
                (search,),
                builtin_tutor_validators(course.content),
                (PromptComposerRegistration(binding.pins.prompt, CanonicalPromptComposer()),),
            ),
            run_store=repository.runs,
            clock=repository.clock,
        )
        return StudyCapabilityGateway(bindings=(binding,), engine=engine)


def _completion_output_fingerprint(value: Mapping[str, object]) -> str:
    return sha256(
        b"study-agent-capability-output-v1\0" + canonical_json_bytes(cast(JsonObject, value))
    ).hexdigest()


def _explanation_product_receipt(
    reference: TutorCapabilityCompletionReference, output: Mapping[str, object]
) -> CapabilityCompletionProductReceipt | None:
    if output.get("status") != "answered":
        return None
    raw_segments = output.get("segments")
    if not isinstance(raw_segments, tuple) or not raw_segments:
        return None
    pieces: list[str] = []
    canonical_ids: set[str] = set()
    for raw_segment in raw_segments:
        if not isinstance(raw_segment, Mapping):
            return None
        text = raw_segment.get("text")
        citations = raw_segment.get("citations")
        if not isinstance(text, str) or not text or text != text.strip():
            return None
        if not isinstance(citations, tuple):
            return None
        labels: list[str] = []
        for citation in citations:
            if not isinstance(citation, Mapping):
                return None
            source_id = citation.get("source_id")
            revision_id = citation.get("revision_id")
            chunk_id = citation.get("chunk_id")
            locator = citation.get("locator")
            quote = citation.get("quoted_snippet")
            if not (
                isinstance(source_id, str)
                and source_id
                and source_id == source_id.strip()
                and isinstance(revision_id, str)
                and revision_id
                and revision_id == revision_id.strip()
                and isinstance(chunk_id, str)
                and chunk_id
                and chunk_id == chunk_id.strip()
                and isinstance(locator, str)
                and locator
                and locator == locator.strip()
                and isinstance(quote, str)
                and quote
                and quote == quote.strip()
            ):
                return None
            labels.append(f"{locator}\n«{quote[:240]}»")
            canonical_ids.update((source_id, revision_id, chunk_id))
        suffix = f"\n\nFonti: {', '.join(labels)}" if labels else ""
        pieces.append(text + suffix)
    content = "\n\n".join(pieces).strip()
    try:
        return CapabilityCompletionProductReceipt(
            reference.capability_identity,
            reference.run_id,
            content,
            tuple(sorted(canonical_ids)),
        )
    except (TypeError, ValueError):
        return None


class _RepositoryTutorToolGateway:
    """Host-owned bridge from a validated tutor decision to harness tools."""

    def __init__(self, repository: LocalRepository) -> None:
        self._repository = repository

    @property
    def manifests(self) -> tuple[HarnessToolManifestView, ...]:
        return self._repository.harness_tools().manifests

    async def invoke(
        self,
        name: str,
        arguments: JsonObject,
        course_id: CourseId,
        session_id: SessionId,
        host_turn_id: str,
    ) -> object:
        surface = self._repository.harness_tools()
        manifest = next((item for item in surface.manifests if item.name == name), None)
        if manifest is None:
            raise ValueError("tutor named an unknown harness tool")
        target_course = course_id
        target_session: SessionId | None = session_id
        if name == "course.create":
            # The model may name a title, never a target identity.  The host
            # deterministically derives a new course stream from this exact
            # turn, so it cannot redirect a write to another learner course.
            target_course = CourseId(
                "course-tutor-sha256:" + sha256(f"{course_id}:{host_turn_id}".encode()).hexdigest()
            )
            target_session = None
        return await surface.invoke(
            name,
            arguments,
            ExecutionContext(
                PrincipalKind.SERVICE,
                "study-agent-tutor-tool-host",
                target_course,
                CorrelationId(f"cardine-tutor-tool-{host_turn_id}"),
                frozenset(manifest.required_capabilities),
                target_session,
                idempotency_key=f"{host_turn_id}:{name}",
            ),
        )


class _RepositoryTutorAuthority:
    def create_context(
        self,
        course_id: CourseId,
        session_id: SessionId,
        capability_id: object,
        action_identity: HostActionIdentity,
    ) -> ExecutionContext:
        del capability_id
        return ExecutionContext(
            PrincipalKind.SERVICE,
            "cardine-tutor-host",
            course_id,
            CorrelationId(f"cardine-tutor-{action_identity.fingerprint}"),
            frozenset({"course:read", "study:ask"}),
            session_id,
            idempotency_key=action_identity.value,
        )


class _RepositoryTutorActionIdentity:
    def issue(
        self,
        host_turn_id: str,
        context_fingerprint: str,
        decision_fingerprint: str,
        decision_generation: int,
    ) -> HostActionIdentity:
        value = sha256(
            (
                "cardine-tutor-action-v1\0"
                f"{host_turn_id}\0{context_fingerprint}\0"
                f"{decision_fingerprint}\0{decision_generation}"
            ).encode()
        ).hexdigest()
        return HostActionIdentity(f"cardine-tutor-action-sha256:{value}")


class ModelAdapterConfigurationError(ValueError):
    """A configured technical model adapter cannot be constructed safely."""


class ModelAdapterBuilder(Protocol):
    def __call__(self, config: ModelAdapterConfig, credential: str | None) -> ModelPort: ...


class ModelAdapterRegistry:
    """Closed-at-composition registry keyed only by technical adapter identity."""

    def __init__(
        self,
        builders: Mapping[str, ModelAdapterBuilder],
        *,
        versions: Mapping[str, str] | None = None,
    ) -> None:
        copied = dict(builders)
        if not copied or any(
            not isinstance(key, str) or not key or key != key.strip() for key in copied
        ):
            raise ValueError("model adapter ids must be unique non-empty trimmed text")
        if any(not callable(builder) for builder in copied.values()):
            raise ValueError("model adapter builders must be callable")
        raw_versions = dict(versions or {})
        if set(raw_versions) - set(copied):
            raise ValueError("model adapter versions cannot name unregistered adapters")
        if any(not isinstance(value, str) for value in raw_versions.values()):
            raise ValueError("model adapter versions must be semantic-version text")
        try:
            parsed_versions = {
                adapter_id: SemanticVersion.parse(raw_versions.get(adapter_id, "1.0.0"))
                for adapter_id in copied
            }
        except ValueError as error:
            raise ValueError("model adapter versions must be semantic versions") from error
        self._builders = MappingProxyType(copied)
        self._versions = MappingProxyType(parsed_versions)

    @property
    def adapter_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._builders))

    def create(
        self,
        config: ModelAdapterConfig,
        environment: Mapping[str, str] | None = None,
    ) -> ModelPort:
        try:
            builder = self._builders[config.adapter_id]
        except KeyError as error:
            raise ModelAdapterConfigurationError(
                "configured model adapter is unavailable"
            ) from error
        source = os.environ if environment is None else environment
        try:
            credential = (
                None if config.credential_env is None else source.get(config.credential_env)
            )
        except Exception:
            raise ModelAdapterConfigurationError(
                "configured model credential could not be resolved"
            ) from None
        if config.credential_env is not None and (
            not isinstance(credential, str) or not credential
        ):
            raise ModelAdapterConfigurationError("configured model credential is unavailable")
        try:
            return builder(config, credential)
        except Exception:
            raise ModelAdapterConfigurationError(
                "configured model adapter could not be constructed"
            ) from None

    def artifact(self, adapter_id: str) -> ArtifactReference:
        try:
            return ArtifactReference(adapter_id, self._versions[adapter_id])
        except KeyError as error:
            raise ModelAdapterConfigurationError(
                "configured model adapter is unavailable"
            ) from error


def default_model_adapters(*, allow_configurable_endpoints: bool = False) -> ModelAdapterRegistry:
    """Return safe built-ins; arbitrary endpoints require explicit host opt-in."""

    builders: dict[str, ModelAdapterBuilder] = {
        GPT_5_6_LUNA_ADAPTER_ID: _openai_gpt56_luna_model,
    }
    versions = {
        GPT_5_6_LUNA_ADAPTER_ID: GPT_5_6_LUNA_ADAPTER_VERSION,
    }
    if allow_configurable_endpoints:
        builders[OPENAI_COMPATIBLE_ADAPTER_ID] = _openai_compatible_model
        versions[OPENAI_COMPATIBLE_ADAPTER_ID] = OPENAI_COMPATIBLE_ADAPTER_VERSION
    return ModelAdapterRegistry(
        builders,
        versions=versions,
    )


def _openai_compatible_model(config: ModelAdapterConfig, credential: str | None) -> ModelPort:
    required = {"endpoint_url", "model_id", "timeout_seconds"}
    allowed = required | {"structured_output_format"}
    if not required <= set(config.settings) or set(config.settings) - allowed:
        raise ModelAdapterConfigurationError(
            "openai-compatible settings must contain endpoint_url, model_id, and "
            "timeout_seconds, with optional structured_output_format"
        )
    endpoint = config.settings["endpoint_url"]
    model_id = config.settings["model_id"]
    timeout = config.settings["timeout_seconds"]
    structured_output_format = config.settings.get("structured_output_format", "json_schema")
    if not isinstance(endpoint, str) or not isinstance(model_id, str):
        raise ModelAdapterConfigurationError("model endpoint and id must be text")
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise ModelAdapterConfigurationError("model timeout must be numeric")
    if structured_output_format not in {"json_schema", "json_object"}:
        raise ModelAdapterConfigurationError(
            "structured_output_format must be json_schema or json_object"
        )
    if config.credential_env is None or credential is None:
        raise ModelAdapterConfigurationError("model adapter requires credential_env")
    try:
        return OpenAICompatibleModel(
            OpenAICompatibleConfig(
                endpoint,
                model_id,
                credential,
                float(timeout),
                ModelCapabilities(structured_output=True),
                structured_output_format=str(structured_output_format),
            )
        )
    except ValueError as error:
        raise ModelAdapterConfigurationError("model adapter configuration is invalid") from error


def _openai_gpt56_luna_model(config: ModelAdapterConfig, credential: str | None) -> ModelPort:
    if set(config.settings) != {"timeout_seconds"}:
        raise ModelAdapterConfigurationError(
            "GPT-5.6 Luna settings must contain only timeout_seconds"
        )
    timeout = config.settings["timeout_seconds"]
    if not isinstance(timeout, (int, float)) or isinstance(timeout, bool):
        raise ModelAdapterConfigurationError("model timeout must be numeric")
    if config.credential_env != "OPENAI_API_KEY" or credential is None:
        raise ModelAdapterConfigurationError("GPT-5.6 Luna adapter requires OPENAI_API_KEY")
    try:
        return OpenAIGpt56LunaModel(OpenAIGpt56LunaConfig(credential, float(timeout)))
    except ValueError as error:
        raise ModelAdapterConfigurationError(
            "GPT-5.6 Luna adapter configuration is invalid"
        ) from error


class _EngineFactory(GroundingEngineFactory):
    def __init__(
        self,
        *,
        model: ModelPort,
        run_store: SQLiteRunStore,
        clock: SystemClock,
        content: CourseSourceContent,
        model_adapter: ArtifactReference,
    ) -> None:
        self._model = model
        self._run_store = run_store
        self._clock = clock
        self._content = content
        self._model_adapter = model_adapter

    def create(self, *, tools: tuple[ToolExecutor, ...]) -> PlaybookEngine:
        return PlaybookEngine(
            engine_version=_V1,
            model_adapter=self._model_adapter,
            state_contract=ArtifactReference("event_state", _V1),
            model=self._model,
            registries=RuntimeRegistries(
                tools,
                (
                    EvidenceSufficiencyValidator(),
                    GroundedAnswerIntegrityValidator(self._content),
                ),
                (PromptComposerRegistration(GROUNDED_ANSWER_PROMPT, CanonicalPromptComposer()),),
            ),
            run_store=self._run_store,
            clock=self._clock,
        )


@dataclass(frozen=True, slots=True)
class CourseRepository:
    content: CourseSourceContent
    retrieval: SQLiteFtsRetrieval
    ingestion: TextIngestionService


class _RepositoryLessonEvidence(LessonEvidencePort):
    """Adapt the existing course FTS index to Cardine lesson navigation."""

    def __init__(self, retrieval: SQLiteFtsRetrieval) -> None:
        self._retrieval = retrieval

    def search(self, source: LessonSource, query: str) -> tuple[LessonChunk, ...]:
        result = self._retrieval.search(
            RetrievalQuery(
                CourseId(source.course_id),
                query,
                limit=8,
                revision_ids=(RevisionId(source.revision_id),),
                include_superseded=True,
            )
        )
        return tuple(
            LessonChunk(
                item.chunk.start_offset,
                item.chunk.end_offset,
                item.text,
                item.chunk.section_path,
            )
            for item in result.evidence
            if str(item.chunk.source_id) == source.source_id
            and str(item.chunk.revision_id) == source.revision_id
        )


class _RepositorySourceCatalog:
    """Complete canonical catalog required by the single repository FTS database."""

    def __init__(
        self,
        course_ids: Callable[[], tuple[CourseId, ...]],
        events: SQLiteEventStore,
        blobs: FilesystemBlobStore,
        source_lifetime: ProjectionSourceLifetimeView,
    ) -> None:
        self._course_ids = course_ids
        self._events = events
        self._blobs = blobs
        self._source_lifetime = source_lifetime

    def _contents(self) -> tuple[CourseSourceContent, ...]:
        return tuple(
            CourseSourceContent(course_id, self._events, self._blobs)
            for course_id in self._course_ids()
        )

    def documents(self, *, include_superseded: bool = False) -> tuple[RetrievalDocument, ...]:
        course_ids = self._course_ids()
        retired_by_course = {
            course_id: self._source_lifetime.retired_source_ids(course_id)
            for course_id in course_ids
        }
        return tuple(
            document
            for course_id in course_ids
            for document in CourseSourceContent(
                course_id, self._events, self._blobs
            ).documents(include_superseded=include_superseded)
            if document.source_id not in retired_by_course[document.course_id]
        )

    def all_documents(self, *, include_superseded: bool = False) -> tuple[RetrievalDocument, ...]:
        return tuple(
            document
            for content in self._contents()
            for document in content.documents(include_superseded=include_superseded)
        )

    def canonical_document(self, chunk_id: ChunkId) -> RetrievalDocument:
        matches = tuple(
            document
            for document in self.all_documents(include_superseded=True)
            if document.chunk.chunk_id == chunk_id
        )
        if len(matches) != 1:
            raise LookupError("canonical chunk was not found uniquely")
        return matches[0]

    def resolve(self, citation: Citation) -> ResolvedCitation:
        document = self.canonical_document(citation.chunk_id)
        return CourseSourceContent(document.course_id, self._events, self._blobs).resolve(citation)

    def contains(self, course_id: CourseId, commitment: SourceCommitment) -> bool:
        """Check a source commitment against the canonical current catalog."""

        if getattr(commitment, "source_id", None) is None:
            return False
        try:
            document = self.canonical_document(commitment.chunk_id)
        except (LookupError, AttributeError):
            return False
        return (
            document.course_id == course_id
            and document.chunk.source_id == commitment.source_id
            and document.chunk.revision_id == commitment.revision_id
            and document.chunk.start_offset == commitment.start_offset
            and document.chunk.end_offset == commitment.end_offset
        )


class _UnconfiguredGeneratedBatchOwner:
    """Explicitly unavailable until a lesson/exam owner is composed."""

    def recover(self, run_id: RunId, context: ExecutionContext) -> VerifiedGeneratedArtifactBatch:
        del run_id, context
        raise RuntimeError("generated artifact owner is not configured")


class _UnconfiguredArtifactDecisionPolicy:
    def decide(self, request: ServiceDecisionPolicyRequest) -> ServiceDecisionPolicyReceipt:
        del request
        raise RuntimeError("service artifact decision policy is not configured")


class LocalRepository:
    """Existing services wired over durable local adapters; no behavior is reimplemented."""

    def __init__(
        self,
        paths: LocalRepositoryPaths,
        config: LocalRepositoryConfig,
        *,
        model_adapters: ModelAdapterRegistry | None = None,
        environment: Mapping[str, str] | None = None,
        observation: RepositoryObservationHandle | None = None,
        recall_scheduler: SchedulingPolicyPort | None = None,
        recall_scheduler_factory: Callable[[], SchedulingPolicyPort] | None = None,
        tutor_host_runner: TutorHostRunner | None = None,
        tutor_continuation_store: TutorContinuationStore | None = None,
    ) -> None:
        if recall_scheduler is not None and recall_scheduler_factory is not None:
            raise TypeError("recall_scheduler and recall_scheduler_factory are mutually exclusive")
        if tutor_host_runner is not None and tutor_continuation_store is None:
            raise TypeError("tutor_host_runner requires an explicit tutor_continuation_store")
        if observation is None:
            validate_local_repository_layout(paths)
            persisted = LocalRepositoryConfig.load(paths.config)
            if persisted != config:
                raise LocalRepositoryError("loaded repository configuration is incompatible")
            blobs = FilesystemBlobStore(paths.blobs)
        else:
            if paths != observation.mutation_paths():
                raise LocalRepositoryError("repository observation paths are incompatible")
            blob_descriptor = observation.directory_descriptor("blobs")
            try:
                blobs = FilesystemBlobStore.from_descriptor(blob_descriptor, read_only=False)
            finally:
                os.close(blob_descriptor)
        events_database = (
            paths.events if observation is None else observation.mutation_database_path("events")
        )
        runs_database = (
            paths.runs if observation is None else observation.mutation_database_path("runs")
        )
        retrieval_database = (
            paths.retrieval
            if observation is None
            else observation.mutation_database_path("retrieval")
        )
        events_guard = (
            None
            if observation is None
            else SQLiteConnectionIdentityGuard(
                observation.database_connection_identity("events"),
                observation.verify_binding,
            )
        )
        runs_guard = (
            None
            if observation is None
            else SQLiteConnectionIdentityGuard(
                observation.database_connection_identity("runs"),
                observation.verify_binding,
            )
        )
        retrieval_guard = (
            None
            if observation is None
            else SQLiteConnectionIdentityGuard(
                observation.database_connection_identity("retrieval"),
                observation.verify_binding,
            )
        )
        self.paths = paths
        self._retrieval_database = retrieval_database
        self._retrieval_connection_identity_guard = retrieval_guard
        self.config = config
        self.clock = SystemClock()
        self.blobs = blobs
        registry = EventRegistry()
        register_course_events(registry)
        register_course_policy_events(registry)
        register_source_revision_events(registry, self.blobs.get)
        register_session_events(registry)
        register_study_context_events(registry)
        register_artifact_events(registry)
        register_assessment_events(registry)
        register_recall_events(registry)
        self.events = SQLiteEventStore(
            events_database, registry, connection_identity_guard=events_guard
        )
        self.runs = SQLiteRunStore(runs_database, connection_identity_guard=runs_guard)
        self.pageindex = PageIndexCoordinator(self.runs)
        self.indexing = IndexingCoordinator(
            NamespacedSQLiteRunStore(self.runs, "cardine-indexing")
        )
        self.provider_consent = ProjectionConsentView(self.events.projection)
        self.source_lifetime = ProjectionSourceLifetimeView(self.events.projection)
        self._source_catalog = _RepositorySourceCatalog(
            self.events.list_course_ids, self.events, self.blobs, self.source_lifetime
        )
        self.artifacts = ProjectionArtifactView(self.events.projection)
        self.courses = ProjectionCourseView(self.events.projection)
        self.course_catalog = ProjectionCourseCatalog(self.events.list_course_ids, self.courses)
        # Startup only reconciles the bounded operational queue.  It never
        # invokes the worker, so read-only status/bootstrap inspection remains
        # load-only and restart stays playable through lexical fallback.
        self._queue_pageindex_backfill()
        self.course_service = CourseService(self.events, self.clock, self.courses)
        self.provider_consent_service = CourseConsentService(
            self.events, self.clock, self.provider_consent, self.courses
        )
        self.source_lifetime_service = SourceLifetimeService(
            self.events,
            self.clock,
            self.source_lifetime,
            self.courses,
            lambda course_id: CourseSourceContent(course_id, self.events, self.blobs),
        )
        self.sessions = ProjectionSessionView(self.events.projection)
        self.session_service = SessionService(self.events, self.clock, self.sessions, self.courses)
        self.artifact_service = ArtifactService(
            self.events,
            self.clock,
            self.artifacts,
            self.sessions,
            _UnconfiguredGeneratedBatchOwner(),
            self._source_catalog,
            _UnconfiguredArtifactDecisionPolicy(),
        )
        self.flashcard_composition: FlashcardProposalComposition | None = None
        self.assistant_turns = ProjectionAssistantTurnView(self.events.projection)
        self.tutor_presentations = ProjectionTutorPresentationView(self.events.projection)
        self.tutor_snapshots = TutorSnapshotReader(self.events, registry)
        self.session_turn_service = SessionTurnService(
            self.events,
            self.clock,
            self.sessions,
            self.assistant_turns,
            self.tutor_presentations,
        )
        self.tutor_continuations = (
            tutor_continuation_store
            if tutor_continuation_store is not None
            else SQLiteTutorContinuationStore(runs_database, connection_identity_guard=runs_guard)
        )
        self.tutor_completion_handoffs = NamespacedSQLiteRunStore(
            self.runs, "tutor-completion-handoff"
        )
        self.conversation: ConversationTurnApplication | None = None
        if tutor_host_runner is not None:
            # The caller supplied both halves of the host composition.  Keep
            # the exact store binding explicit at this boundary so an
            # injected runner cannot silently resume from a different store.
            self.conversation = self.conversation_application(
                tutor_host_runner, continuation_store=self.tutor_continuations
            )
        self.study_context = ProjectionStudyContextView(self.events.projection)
        self.assessments = ProjectionAssessmentView(self.events.projection)
        self.learner_evidence = ProjectionLearnerEvidenceView(self.assessments)
        self.assessment_service = AssessmentService(
            self.events,
            self.clock,
            self.assessments,
            self.artifacts,
            self.sessions,
            ExactClosedGradingPolicy(),
        )
        self.study_context_service = StudyContextService(
            self.events, self.clock, self.study_context, self.courses, self.sessions
        )
        self.recall_composition = compose_recall(
            events=self.events,
            load_projection=self.events.projection,
            clock=self.clock,
            scheduler=recall_scheduler,
            scheduler_factory=recall_scheduler_factory,
        )
        self.recall: RecallComposition | None = (
            self.recall_composition if self.recall_composition.availability.available else None
        )
        self.recall_availability: RecallAvailability = self.recall_composition.availability
        self.study_readiness = StudyReadinessView(
            self.events.projection,
            self.clock,
            recall_available=self.recall_availability.available,
        )
        self._model_adapters = model_adapters or default_model_adapters()
        self._environment = environment
        if observation is not None:
            observation.adopt_created_database_bindings()

    def conversation_application(
        self,
        runner: TutorHostRunner,
        *,
        continuation_store: TutorContinuationStore,
        completion_handlers: CapabilityCompletionHandlerRegistry | None = None,
        completion_handoff_store: TutorCompletionHandoffStore | None = None,
    ) -> ConversationTurnApplication:
        """Compose the host-independent conversation owner with injected host work.

        Provider, gateway, and authority selection remain outside this
        repository composition root.  Callers that do not configure a host
        continue to use the existing grounding-only services unchanged.  The
        explicit store must be the same operational store used to construct
        ``runner``; the repository deliberately does not inspect runner
        internals to infer or override that binding.
        """
        selected_handoff_store = (
            self.tutor_completion_handoffs
            if completion_handoff_store is None
            else completion_handoff_store
        )
        runner_handoff_store = getattr(runner, "completion_handoff_store", None)
        if runner_handoff_store is not None and runner_handoff_store is not selected_handoff_store:
            raise TypeError(
                "repository composition requires the runner's exact completion handoff store"
            )
        return ConversationTurnApplication(
            self.session_turn_service,
            runner,
            self.tutor_snapshots,
            self.sessions,
            self.tutor_presentations,
            continuation_store,
            completion_handlers=completion_handlers,
            completion_handoff_store=selected_handoff_store,
            fallback_message_policy=_cardine_fallback_message,
        )

    def tutor_conversation(
        self,
        course_id: CourseId,
        *,
        session_id: SessionId | None = None,
        lesson_pin: SourcePin | None = None,
    ) -> ConversationTurnApplication:
        """Compose the private provider-backed tutor chat for one course.

        The provider adapter receives only the redacted host context.  The
        trusted gateway/authority/identity/store stay server-side and the
        application remains the sole conversation orchestrator.

        ``lesson_pin`` attaches one validated lesson to the conversation.  Every
        turn then retrieves evidence from that lesson only, instead of resolving
        a lesson from the wording of the turn.
        """
        if not isinstance(course_id, CourseId):
            raise TypeError("tutor conversation requires a CourseId")
        self.courses.get(course_id)
        if lesson_pin is not None:
            if not isinstance(lesson_pin, SourcePin) or lesson_pin.course_id != str(course_id):
                raise ValueError("lesson pin belongs to another course")
            self.validate_lesson_pin(lesson_pin)
        if self.conversation is not None:
            return self.conversation
        if self.config.model is None:
            raise ModelAdapterConfigurationError("no model adapter is configured")
        model = ConsentModelPort(
            self._model_adapters.create(self.config.model, self._environment),
            course_id,
            self.provider_consent,
        )
        selected_session_id = (
            session_id if session_id is not None else self.sessions.list_sessions(course_id)[0].id
        )
        flashcards: FlashcardProposalComposition | None = None
        try:
            flashcards = FlashcardProposalComposition(
                course_id=course_id,
                session_id=selected_session_id,
                content=self.for_course(course_id).content,
                course_profile=course_profile_manifest(self.courses.get(course_id)),
                model=model,
                model_adapter=self._model_adapters.artifact(self.config.model.adapter_id),
                runs=self.runs,
                clock=self.clock,
                artifact_service=self.artifact_service,
                source_commitments=self._source_catalog,
                sessions=self.sessions,
                retired_source_ids=lambda: self.source_lifetime.retired_source_ids(course_id),
            )
            self.artifact_service = ArtifactService(
                self.events,
                self.clock,
                self.artifacts,
                self.sessions,
                flashcards.runtime.batches,
                self._source_catalog,
                _UnconfiguredArtifactDecisionPolicy(),
            )
            flashcards.attach_artifact_service(self.artifact_service)
            self.flashcard_composition = flashcards
        except Exception:
            # Capability discovery is fail-closed.  Explain remains available
            # when this optional lesson composition cannot be assembled.
            flashcards = None
        gateway = _RepositoryTutorGateway(
            self,
            course_id,
            selected_session_id,
            model,
            self._model_adapters.artifact(self.config.model.adapter_id),
            flashcards,
            lesson_pin,
        )
        runner = TutorHostRunner(
            FlashcardProfileRoutingTutorDecisionPort(
                SourceGroundedTutorDecisionPort(ModelTutorDecisionPort(model))
            ),
            self.tutor_snapshots,
            self.learner_evidence,
            gateway,
            _RepositoryTutorAuthority(),
            _RepositoryTutorActionIdentity(),
            self.tutor_continuations,
            TutorHostLimits(
                max_decisions=4,
                max_provider_attempts_per_decision=2,
                max_stale_refreshes=2,
                max_emitted_text_chars=4_000,
            ),
            context_assembler=TutorHostContextAssembler(
                self.tutor_snapshots,
                self.learner_evidence,
                gateway,
                self.tutor_presentations,
                _RepositoryTutorToolGateway(self),
            ),
            completion_handoff_store=self.tutor_completion_handoffs,
            tool_gateway=_RepositoryTutorToolGateway(self),
        )
        completion_entries: list[tuple[str, str, CapabilityCompletionHandler]] = [
            (
                f"{EXPLAIN_CONCEPT_MANIFEST.id.value}@{EXPLAIN_CONCEPT_MANIFEST.version.major}",
                EXPLAIN_CONCEPT_MANIFEST.fingerprint,
                gateway,
            )
        ]
        if flashcards is not None:
            completion_entries.append(
                (flashcards.manifest.identity, flashcards.manifest.fingerprint, flashcards)
            )
        handlers = CapabilityCompletionHandlerRegistry(tuple(completion_entries))
        return self.conversation_application(
            runner,
            continuation_store=self.tutor_continuations,
            completion_handlers=handlers,
            completion_handoff_store=self.tutor_completion_handoffs,
        )

    @classmethod
    def open(
        cls,
        root: str | Path,
        *,
        model_adapters: ModelAdapterRegistry | None = None,
        environment: Mapping[str, str] | None = None,
        recall_scheduler: SchedulingPolicyPort | None = None,
        recall_scheduler_factory: Callable[[], SchedulingPolicyPort] | None = None,
        tutor_host_runner: TutorHostRunner | None = None,
        tutor_continuation_store: TutorContinuationStore | None = None,
    ) -> LocalRepository:
        paths = LocalRepositoryPaths.at(root)
        config = LocalRepositoryConfig.load(paths.config)
        return cls(
            paths,
            config,
            model_adapters=model_adapters,
            environment=environment,
            recall_scheduler=recall_scheduler,
            recall_scheduler_factory=recall_scheduler_factory,
            tutor_host_runner=tutor_host_runner,
            tutor_continuation_store=tutor_continuation_store,
        )

    @classmethod
    def from_observation(
        cls,
        observation: RepositoryObservationHandle,
        config: LocalRepositoryConfig,
        *,
        model_adapters: ModelAdapterRegistry | None = None,
        environment: Mapping[str, str] | None = None,
        recall_scheduler: SchedulingPolicyPort | None = None,
        recall_scheduler_factory: Callable[[], SchedulingPolicyPort] | None = None,
        tutor_host_runner: TutorHostRunner | None = None,
        tutor_continuation_store: TutorContinuationStore | None = None,
    ) -> LocalRepository:
        """Compose mutable adapters while retaining an inspected repository owner."""
        if not isinstance(observation, RepositoryObservationHandle):
            raise TypeError("observation must be a RepositoryObservationHandle")
        if not isinstance(config, LocalRepositoryConfig):
            raise TypeError("config must be a LocalRepositoryConfig")
        return cls(
            observation.mutation_paths(),
            config,
            model_adapters=model_adapters,
            environment=environment,
            observation=observation,
            recall_scheduler=recall_scheduler,
            recall_scheduler_factory=recall_scheduler_factory,
            tutor_host_runner=tutor_host_runner,
            tutor_continuation_store=tutor_continuation_store,
        )

    def for_course(self, course_id: CourseId) -> CourseRepository:
        content = CourseSourceContent(course_id, self.events, self.blobs)
        return CourseRepository(
            content,
            SQLiteFtsRetrieval(
                self._retrieval_database,
                self._source_catalog,
                connection_identity_guard=self._retrieval_connection_identity_guard,
            ),
            TextIngestionService(
                blobs=self.blobs,
                events=self.events,
                clock=self.clock,
                courses=self.courses,
            ),
        )

    def _pageindex_revisions(
        self, course_id: CourseId | None = None, *, limit: int | None = None
    ) -> tuple[PageIndexRevision, ...]:
        revisions: list[PageIndexRevision] = []
        course_ids = (course_id,) if course_id is not None else self.events.list_course_ids()
        for selected_course in course_ids:
            retired = self.source_lifetime.retired_source_ids(selected_course)
            state = self.events.projection(selected_course).state
            raw_sources = state.get("sources", {})
            if not isinstance(raw_sources, Mapping):
                raise LocalRepositoryError("source projection is incompatible")
            for source_id, raw_source in sorted(raw_sources.items()):
                if not isinstance(source_id, str) or not isinstance(raw_source, Mapping):
                    raise LocalRepositoryError("source projection is incompatible")
                if SourceId(source_id) in retired:
                    continue
                revision_id = raw_source.get("current_revision_id")
                raw_revisions = raw_source.get("revisions")
                if not isinstance(revision_id, str) or not isinstance(raw_revisions, Mapping):
                    raise LocalRepositoryError("source projection is incompatible")
                raw_revision = raw_revisions.get(revision_id)
                if not isinstance(raw_revision, Mapping):
                    raise LocalRepositoryError("source projection is incompatible")
                raw_manifest = raw_revision.get("source")
                if not isinstance(raw_manifest, Mapping):
                    raise LocalRepositoryError("source projection is incompatible")
                if raw_manifest.get("kind") != "markdown":
                    continue
                raw_blob = raw_manifest.get("normalized_blob")
                if not isinstance(raw_blob, Mapping):
                    raise LocalRepositoryError("source projection is incompatible")
                blob_id = raw_blob.get("id")
                checksum = raw_blob.get("checksum_sha256")
                byte_length = raw_blob.get("byte_length")
                if (
                    not isinstance(blob_id, str)
                    or not isinstance(checksum, str)
                    or type(byte_length) is not int
                ):
                    raise LocalRepositoryError("source projection is incompatible")
                try:
                    reference = BlobRef(BlobId(blob_id), checksum, byte_length)
                    content = self.blobs.get(reference).decode("utf-8", errors="strict")
                except BlobNotFoundError as error:
                    raise SourceContentError(
                        SourceContentErrorCode.NOT_FOUND,
                        "source projection content is unavailable",
                    ) from error
                except (BlobIntegrityError, UnsafeBlobPathError, UnicodeError, ValueError) as error:
                    raise SourceContentError(
                        SourceContentErrorCode.INTEGRITY_ERROR,
                        "source projection content failed integrity validation",
                    ) from error
                digest = sha256(content.encode("utf-8")).hexdigest()
                revisions.append(
                    PageIndexRevision(
                        str(selected_course),
                        source_id,
                        revision_id,
                        content,
                        digest,
                    )
                )
                if limit is not None and len(revisions) >= limit:
                    return tuple(revisions)
        return tuple(
            sorted(revisions, key=lambda item: (item.course_id, item.source_id, item.revision_id))
        )

    def _queue_pageindex_backfill(self, course_id: CourseId | None = None) -> None:
        for revision in self._pageindex_revisions(
            course_id, limit=_PAGEINDEX_RECONCILE_BUDGET
        ):
            self.pageindex.request(revision)

    def reconcile_pageindex(
        self, course_id: CourseId | None = None, *, budget: int = _PAGEINDEX_ADMISSION_BUDGET
    ) -> tuple[PageIndexProjection, ...]:
        """Process a small restart/admission backfill through the isolated worker."""

        if type(budget) is not int or not 0 <= budget <= _PAGEINDEX_RECONCILE_BUDGET:
            raise ValueError("PageIndex reconcile budget is outside the bound")
        revisions = self._pageindex_revisions(course_id, limit=budget)
        return self.pageindex.reconcile(revisions, budget=budget)

    def pageindex_status(self, course_id: CourseId) -> tuple[PageIndexProjection, ...]:
        """Return per-revision status without creating or processing projections."""

        result: list[PageIndexProjection] = []
        for revision in self._pageindex_revisions(course_id):
            try:
                result.append(self.pageindex.load(revision))
            except KeyError:
                # A projection can be absent when an older repository was
                # opened before this derived surface existed or when the
                # bounded startup queue has not reached this revision. Report
                # the truthful queued state without mutating or invoking a
                # worker.
                result.append(
                    PageIndexProjection(
                        revision.course_id,
                        revision.source_id,
                        revision.revision_id,
                        revision.content_sha256,
                        PageIndexStatus.QUEUED,
                        0,
                    )
                )
        return tuple(result)

    def _pageindex_revision(
        self, course_id: CourseId, source_id: SourceId, revision_id: RevisionId
    ) -> PageIndexRevision:
        for revision in self._pageindex_revisions(course_id):
            if revision.source_id == str(source_id) and revision.revision_id == str(revision_id):
                return revision
        raise LookupError("active Markdown revision was not found")

    def rebuild_pageindex(
        self, course_id: CourseId, source_id: SourceId, revision_id: RevisionId
    ) -> PageIndexProjection:
        revision = self._pageindex_revision(course_id, source_id, revision_id)
        self.pageindex.rebuild(revision)
        return self.pageindex.process(revision)

    def disable_pageindex(
        self, course_id: CourseId, source_id: SourceId, revision_id: RevisionId
    ) -> PageIndexProjection:
        revision = self._pageindex_revision(course_id, source_id, revision_id)
        return self.pageindex.disable(revision)

    def enable_pageindex(
        self, course_id: CourseId, source_id: SourceId, revision_id: RevisionId
    ) -> PageIndexProjection:
        revision = self._pageindex_revision(course_id, source_id, revision_id)
        return self.pageindex.enable(revision)

    def _lesson_sources(self, course_id: CourseId) -> tuple[LessonSource, ...]:
        retired = self.source_lifetime.retired_source_ids(course_id)
        content = self.for_course(course_id).content
        records = tuple(
            record
            for record in content.catalog()
            if record.is_current_revision and record.source.source_id not in retired
        )
        if len(records) > _LESSON_SEARCH_SOURCE_BUDGET:
            raise ValueError("lesson search exceeds the bounded source budget")
        documents = tuple(content.documents())
        catalog_fingerprint = retrieval_catalog_fingerprint(documents)
        return tuple(
            LessonSource(
                str(course_id),
                str(record.source.source_id),
                str(record.source.revision_id),
                record.source.title,
                record.source.kind.value,
                record.text,
                sha256(record.text.encode("utf-8")).hexdigest(),
                catalog_fingerprint,
                tuple(
                    LessonChunk(
                        chunk.start_offset,
                        chunk.end_offset,
                        record.text[chunk.start_offset : chunk.end_offset],
                        chunk.section_path,
                    )
                    for chunk in record.chunks
                ),
            )
            for record in records
        )

    def search_lessons(self, course_id: CourseId, query: str) -> LessonSearchResult:
        sources = self._lesson_sources(course_id)
        retrieval = self.for_course(course_id).retrieval
        statuses = {
            (item.source_id, item.revision_id): item
            for item in self.pageindex_status(course_id)
        }
        fallback_sources = tuple(
            source
            for source in sources
            if not (
                source.kind.casefold() == "markdown"
                and statuses.get((source.source_id, source.revision_id)) is not None
                and statuses[(source.source_id, source.revision_id)].status
                is PageIndexStatus.READY
            )
        )
        lexical = LessonSelectionService(_RepositoryLessonEvidence(retrieval)).search(
            str(course_id), query, fallback_sources
        )
        candidates = list(lexical.candidates)
        for source in sources:
            if source.kind.casefold() != "markdown":
                continue
            projection = statuses.get((source.source_id, source.revision_id))
            if projection is None or projection.status is not PageIndexStatus.READY:
                continue
            for item in projection.candidates:
                if not lesson_title_matches(item.title, query):
                    continue
                identity = "\0".join(
                    (
                        source.course_id,
                        source.source_id,
                        source.revision_id,
                        str(item.start_offset),
                        str(item.end_offset),
                        item.title,
                    )
                ).encode()
                candidates.append(
                    LessonCandidate(
                        f"lesson-sha256:{sha256(identity).hexdigest()}",
                        source.course_id,
                        source.source_id,
                        source.revision_id,
                        item.title,
                        item.start_offset,
                        item.end_offset,
                        source.content_sha256,
                        source.catalog_fingerprint,
                    )
                )
        ordered = tuple(sorted(candidates, key=lambda item: (item.source_id, item.start_offset)))
        disposition = (
            SearchDisposition.NOT_FOUND
            if not ordered
            else SearchDisposition.UNIQUE
            if len(ordered) == 1
            else SearchDisposition.AMBIGUOUS
        )
        return LessonSearchResult(disposition, ordered)

    def select_lesson(
        self, course_id: CourseId, query: str, candidate_id: str
    ) -> SourcePin:
        result = self.search_lessons(course_id, query)
        service = LessonSelectionService(
            _RepositoryLessonEvidence(self.for_course(course_id).retrieval)
        )
        return service.select(candidate_id, result)

    def resolve_lesson_scope(self, course_id: CourseId, query: str) -> SourcePin | None:
        """Resolve one explicit lesson reference without silently choosing ambiguity."""

        if not isinstance(course_id, CourseId):
            raise TypeError("lesson scope requires a CourseId")
        if not isinstance(query, str) or not query.strip():
            raise ValueError("lesson scope query is invalid")
        normalized = query.casefold()
        references = tuple(
            dict.fromkeys(
                match.group(0).strip()
                for match in re.finditer(
                    r"(?:\blezione\s+(?:numero\s+)?\d+\b|"
                    r"\bl[\s_-]*0*\d+(?=$|[\s_./-]))",
                    normalized,
                )
            )
        )
        if not references:
            return None
        candidates: list[LessonCandidate] = []
        for reference in references:
            candidates.extend(self.search_lessons(course_id, reference).candidates)
        unique = {item.candidate_id: item for item in candidates}
        if not unique:
            return None
        if len(unique) != 1:
            raise LessonSelectionError("lesson scope is ambiguous")
        candidate = next(iter(unique.values()))
        pin = self.select_lesson(course_id, candidate.section_title, candidate.candidate_id)
        self.validate_lesson_pin(pin)
        return pin

    def validate_lesson_pin(self, pin: SourcePin) -> LessonSource:
        sources = self._lesson_sources(CourseId(pin.course_id))
        source = LessonSelectionService(
            _RepositoryLessonEvidence(self.for_course(CourseId(pin.course_id)).retrieval)
        ).validate_pin(pin, sources)
        if source.kind.casefold() == "markdown":
            candidates = self.search_lessons(
                CourseId(pin.course_id), pin.section_title
            ).candidates
            if not any(
                item.source_id == pin.source_id
                and item.revision_id == pin.revision_id
                and item.section_title == pin.section_title
                and item.start_offset == pin.start_offset
                and item.end_offset == pin.end_offset
                for item in candidates
            ):
                raise ValueError("lesson pin is not a current canonical candidate")
        elif not (
            pin.section_title == source.title
            and any(
                chunk.start_offset == pin.start_offset
                and chunk.end_offset == pin.end_offset
                for chunk in source.chunks
            )
        ):
            raise ValueError("lesson pin is not a current canonical candidate")
        return source

    def pageindex_summary(self, course_id: CourseId) -> JsonObject:
        statuses = self.pageindex_status(course_id)
        rows = tuple(
            {
                "source_id": item.source_id,
                "revision_id": item.revision_id,
                "status": item.status.value,
                "attempt": item.attempt,
                "candidate_count": len(item.candidates),
                "error_code": item.error_code,
            }
            for item in statuses
        )
        return {
            "status": (
                "empty"
                if not rows
                else "ready"
                if all(item["status"] == "ready" for item in rows)
                else "degraded"
            ),
            "active_revisions": len(rows),
            "items": rows,
        }

    def rebuild_retrieval(self) -> IndexReceipt:
        """Rebuild the one discardable index from the complete canonical catalog."""
        retrieval = SQLiteFtsRetrieval(
            self._retrieval_database,
            self._source_catalog,
            connection_identity_guard=self._retrieval_connection_identity_guard,
        )
        documents = tuple(self._source_catalog.documents(include_superseded=True))
        receipt = retrieval.rebuild(documents)
        self._queue_pageindex_backfill()
        return receipt

    def queue_indexing(self) -> IndexingRecord:
        """Queue the current canonical catalog without performing derived work."""

        documents = tuple(self._source_catalog.documents(include_superseded=True))
        return self.indexing.queue(retrieval_catalog_fingerprint(documents))

    def indexing_status(self) -> IndexingRecord | None:
        """Read durable derived-index progress without rebuilding either index."""

        return self.indexing.get()

    def reconcile_indexing(self) -> IndexingRecord:
        """Run one queued target through atomic FTS and bounded structure work."""

        queued = self.queue_indexing()
        if queued.status in {IndexingStatus.READY, IndexingStatus.DEGRADED}:
            return queued
        if queued.status is IndexingStatus.INDEXING:
            recovered = self.indexing.transition(
                queued,
                status=IndexingStatus.QUEUED,
                phase=IndexingPhase.QUEUED,
                error_code=None,
            )
            queued = recovered or self.indexing.get() or queued
            if queued.status is IndexingStatus.INDEXING:
                return queued
        active = self.indexing.transition(
            queued, status=IndexingStatus.INDEXING, phase=IndexingPhase.LEXICAL
        )
        if active is None:
            return self.indexing.get() or queued
        try:
            receipt = self.rebuild_retrieval()
        except (OSError, RuntimeError, ValueError):
            failed = self.indexing.transition(
                active,
                status=IndexingStatus.FAILED,
                phase=IndexingPhase.COMPLETE,
                error_code="lexical_index_failed",
            )
            return failed or self.indexing.get() or active
        structural = self.indexing.transition(
            active,
            status=IndexingStatus.INDEXING,
            phase=IndexingPhase.STRUCTURE,
            indexed_chunks=receipt.indexed_chunks,
        )
        if structural is None:
            return self.indexing.get() or active
        try:
            for course_id in self.events.list_course_ids():
                self.reconcile_pageindex(course_id)
            pageindex = tuple(
                item
                for course_id in self.events.list_course_ids()
                for item in self.pageindex_status(course_id)
            )
        except (SourceContentError, OSError, RuntimeError, ValueError):
            failed = self.indexing.transition(
                structural,
                status=IndexingStatus.FAILED,
                phase=IndexingPhase.COMPLETE,
                indexed_chunks=receipt.indexed_chunks,
                error_code="structural_index_failed",
            )
            return failed or self.indexing.get() or structural
        degraded = any(item.status is not PageIndexStatus.READY for item in pageindex)
        terminal = self.indexing.transition(
            structural,
            status=IndexingStatus.DEGRADED if degraded else IndexingStatus.READY,
            phase=IndexingPhase.COMPLETE,
            indexed_chunks=receipt.indexed_chunks,
            error_code="structural_index_degraded" if degraded else None,
        )
        return terminal or self.indexing.get() or structural

    def course_index_receipt(
        self, course_id: CourseId, repository_receipt: IndexReceipt
    ) -> IndexReceipt:
        """Bind an audited repository index version to one ask service's course reads."""
        if (
            type(repository_receipt) is not IndexReceipt
            or type(repository_receipt.indexed_chunks) is not int
            or type(repository_receipt.index_version) is not str
            or type(repository_receipt.catalog_fingerprint) is not str
        ):
            raise LocalRepositoryError("repository retrieval receipt is incompatible")
        documents = tuple(
            document
            for document in self._source_catalog.documents(include_superseded=True)
            if document.course_id == course_id
        )
        retrieval = SQLiteFtsRetrieval(
            self._retrieval_database,
            self._source_catalog,
            connection_identity_guard=self._retrieval_connection_identity_guard,
        )
        try:
            audited = retrieval.index(())
        except (OSError, RuntimeError, ValueError) as error:
            raise LocalRepositoryError(
                "retrieval index does not match the canonical repository catalog"
            ) from error
        if audited != repository_receipt:
            raise LocalRepositoryError("repository retrieval receipt is stale or incompatible")
        return IndexReceipt(
            len(documents),
            repository_receipt.index_version,
            retrieval_catalog_fingerprint(documents),
        )

    def grounding_service(
        self,
        course_id: CourseId,
        index_receipt: IndexReceipt,
        *,
        lesson_pin: SourcePin | None = None,
    ) -> GroundingAskService:
        retrieval: RetrievalPort = self.for_course(course_id).retrieval
        if lesson_pin is not None:
            if not isinstance(lesson_pin, SourcePin) or lesson_pin.course_id != str(course_id):
                raise ValueError("lesson pin belongs to another course")
            source = self.validate_lesson_pin(lesson_pin)
            if not any(
                chunk.start_offset >= lesson_pin.start_offset
                and chunk.end_offset <= lesson_pin.end_offset
                for chunk in source.chunks
            ):
                raise ValueError("lesson pin contains no complete canonical chunk")
            retrieval = _PinnedRetrieval(retrieval, lesson_pin)
        if self.config.model is None:
            raise ModelAdapterConfigurationError("no model adapter is configured")
        course = self.for_course(course_id)
        model = ConsentModelPort(
            self._model_adapters.create(self.config.model, self._environment),
            course_id,
            self.provider_consent,
        )
        model_adapter = self._model_adapters.artifact(self.config.model.adapter_id)
        engine_factory = _EngineFactory(
            model=model,
            run_store=self.runs,
            clock=self.clock,
            content=course.content,
            model_adapter=model_adapter,
        )
        pins = VersionPins(
            ArtifactReference(GROUNDED_ANSWER_SKILL.id, GROUNDED_ANSWER_SKILL.version),
            ArtifactReference(GROUNDED_ANSWER_FLOW.id, GROUNDED_ANSWER_FLOW.version),
            GROUNDED_ANSWER_PROMPT,
            (
                ToolBehaviorPin("session.get_context", _V1),
                ToolBehaviorPin("source.search", _V1),
            ),
            model_adapter,
            ArtifactReference("event_state", _V1),
        )
        finalizer = GroundedSessionFinalizer(
            self.events,
            self.clock,
            self.sessions,
            course.content,
            GROUNDED_ANSWER_SKILL.state_write_policy,
        )
        return GroundingAskService(
            courses=self.courses,
            session_service=self.session_service,
            sessions=self.sessions,
            retrieval=retrieval,
            catalog=course.content,
            content=course.content,
            finalizer=finalizer,
            engine_factory=engine_factory,
            run_store=self.runs,
            configuration=GroundingAskConfiguration(
                pins,
                index_receipt,
                lesson_pin=lesson_pin,
            ),
            events=self.events,
        )

    async def propose_flashcards_for_pin(
        self,
        course_id: CourseId,
        session_id: SessionId,
        pin: SourcePin,
        query: str,
        context: ExecutionContext,
    ) -> CapabilityCompletionProductReceipt:
        """Generate and settle one lesson-scoped flashcard proposal batch.

        Pin validation and consent happen before composing the provider model.
        The capability itself remains the existing profile-dispatched
        Harness capability; only its Cardine-owned source view is narrowed.
        """

        if not isinstance(pin, SourcePin) or pin.course_id != str(course_id):
            raise ValueError("lesson pin belongs to another course")
        if context.course_id != course_id or context.session_id != session_id:
            raise ValueError("flashcard request context is outside the selected course")
        session = self.sessions.get_session(course_id, session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise ValueError("flashcard generation requires an active session")
        source = self.validate_lesson_pin(pin)
        if not any(
            chunk.start_offset >= pin.start_offset and chunk.end_offset <= pin.end_offset
            for chunk in source.chunks
        ):
            raise ValueError("lesson pin contains no complete canonical chunk")
        if not isinstance(query, str) or not query.strip() or len(query) > 4_000:
            raise ValueError("flashcard query is invalid")
        request_id = context.idempotency_key
        if request_id is None:
            raise ValueError("flashcard request requires an idempotency key")
        consent = self.provider_consent.get(course_id)
        if consent is None or not consent.granted:
            raise ProviderConsentRequiredError("provider consent is required")
        context = replace(
            context,
            requested_capabilities=context.requested_capabilities
            | frozenset({"course:read", "study:ask"}),
        )
        sequence = self.events.projection(course_id).sequence
        learner = self.session_turn_service.record_learner_turn(
            query.strip(), context, sequence
        )
        service_context = replace(
            context,
            principal_kind=PrincipalKind.SERVICE,
            principal_id="cardine-selected-lesson-flashcards",
        )
        self.tutor_conversation(course_id, session_id=session_id)
        composition = self.flashcard_composition
        if composition is None:
            raise ValueError("flashcard capability is not executable")
        inputs: JsonObject = {
            "query": query.strip(),
            "scope": pin.section_title,
            "language": "it",
            "candidate_ceiling": 24,
            "continuation_summary_json": None,
        }
        scoped = composition.for_pin(pin, learner.id)
        scoped.attach_artifact_service(
            ArtifactService(
                self.events,
                self.clock,
                self.artifacts,
                self.sessions,
                scoped.runtime.batches,
                self._source_catalog,
                self.artifact_service._decision_policy,
            )
        )
        outcome = await scoped.start(inputs, service_context)
        if not isinstance(outcome, CompletedCapabilityOutcome):
            raise RuntimeError("flashcard generation did not complete with verified output")
        reference = TutorCapabilityCompletionReference(
            scoped.manifest.identity,
            scoped.manifest.fingerprint,
            outcome.run.run_id,
            capability_output_fingerprint(outcome.output),
            capability_retry_fingerprint(request_id),
        )
        receipt = scoped.recover(reference, service_context)
        if receipt is None:
            raise RuntimeError("verified flashcard proposal could not be settled")
        return receipt

    def study_tools(self, course_id: CourseId) -> StudyToolRegistry:
        """Compose the exact public tool registry from this repository's services."""
        from study_agent.tools import StudyToolRegistry
        from study_agent.tools.builtin import GroundingAskServiceProvider

        course = self.for_course(course_id)

        def resolve_grounding() -> GroundingAskService:
            if self.config.model is None:
                raise GroundingAskError(
                    GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                    "grounding requires a configured model adapter",
                )
            try:
                receipt = self.rebuild_retrieval()
                return self.grounding_service(
                    course_id, self.course_index_receipt(course_id, receipt)
                )
            except ModelAdapterConfigurationError as error:
                raise GroundingAskError(
                    GroundingAskErrorCode.INCOMPATIBLE_RUNTIME,
                    "grounding model configuration is unavailable",
                ) from error

        return StudyToolRegistry(
            courses=self.courses,
            catalog=course.content,
            retrieval=course.retrieval,
            content=course.content,
            sessions=self.session_service,
            grounding=GroundingAskServiceProvider(resolve_grounding),
        )

    def harness_tools(self) -> HarnessToolSurface:
        """Compose Cardine's private product operations once per repository.

        This does not replace the released seven-tool public registry; it is
        the shared canonical surface for the repository UI and tutor host.
        """
        from cardine.application.tool_surface import HarnessToolOwner
        from study_agent.application import HarnessToolSurface

        return HarnessToolSurface(cast(HarnessToolOwner, self))

    def close(self) -> None:
        self.blobs.close()

    def __enter__(self) -> LocalRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()


__all__ = [
    "CourseRepository",
    "LocalRepository",
    "LocalRepositoryError",
    "LocalRepositoryPaths",
    "ModelAdapterBuilder",
    "ModelAdapterConfigurationError",
    "ModelAdapterRegistry",
    "default_model_adapters",
    "initialize_local_repository",
]
