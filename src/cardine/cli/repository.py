"""Auditable composition root for one local study-agent repository."""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass, replace
from hashlib import sha256
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol, cast

from cardine.adapters.model.retrieval_query_recovery import RetrievalQueryRecovery
from cardine.adapters.pageindex import PageIndexCoordinator, PageIndexRevision
from cardine.application.capability_completion import MAX_COMPLETION_CONTENT_CHARS
from cardine.application.conversation_history import ConversationHistoryReader
from cardine.application.flashcard_proposals import FlashcardProposalComposition
from cardine.application.indexing import (
    IndexingCoordinator,
    IndexingPhase,
    IndexingRecord,
    IndexingStatus,
)
from cardine.application.study_memory import StudyMemoryArchive
from cardine.courses import (
    CourseService,
    ProjectionCourseCatalog,
    ProjectionCourseView,
    course_profile_manifest,
    register_course_events,
)
from cardine.diagnostics import add_settled, begin_activity, finish_activity
from cardine.hosts import (
    ClarificationRecoveryTutorDecisionPort,
    HostActionIdentity,
    InvokeToolDecision,
    SourceGroundedTutorDecisionPort,
    TutorCapabilityCompletionReference,
    TutorHostContextAssembler,
    TutorHostLimits,
    TutorHostRunner,
    TutorHostRunStatus,
    decision_fingerprint,
)
from cardine.hosts.flashcard_routing import FlashcardProfileRoutingTutorDecisionPort
from cardine.hosts.scope_resolution import recent_explicit_lesson_references
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
from cardine.materials import (
    MaterialGenerationService,
    MaterialGenerationStale,
    MaterialGenerationState,
    MaterialVerifiedBatchAdapter,
    PinnedTranscriptInput,
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
    ContentOrigin,
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
    SourceKind,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.grounding import (
    EvidenceSufficiencyValidator,
    GroundedAnswerIntegrityValidator,
)
from study_agent.ingestion import (
    SOURCE_REVISION_INGESTED,
    SOURCE_REVISION_SCHEMA_VERSION,
    TextIngestionService,
    decode_source_revision_event,
    register_source_revision_events,
)
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
    SourceRevisionRecord,
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
_SCHEMA_INCOMPATIBLE_MESSAGE = (
    "Il provider ha rifiutato il formato strutturato richiesto da Cardine. "
    "La richiesta non dipende dalle evidenze del corso e non va ripetuta invariata."
)
_PROVIDER_CONFIGURATION_MESSAGE = (
    "Il modello non è disponibile con la configurazione corrente. "
    "Controlla chiave API, autorizzazioni e modello nelle Impostazioni."
)
_SCOPE_FAILURE_MESSAGE = (
    "Non sono riuscito a mantenere il riferimento alla lezione o alla fonte selezionata. "
    "Seleziona di nuovo la lezione e riprova."
)
_PUBLICATION_FAILURE_MESSAGE = (
    "Il lavoro è stato completato, ma Cardine non è riuscito a pubblicarne il risultato. "
    "Il contenuto non verrà rigenerato automaticamente."
)

_PAGEINDEX_RECONCILE_BUDGET = 32
_PAGEINDEX_ADMISSION_BUDGET = 4
_LESSON_SEARCH_SOURCE_BUDGET = 32

HARNESS_TOOL_LABELS = {
    "course.create": "Registro nel repository",
    "course.list": "Leggo il repository",
    "session.start": "Avvio la sessione",
    "source.ingest": "Registro la fonte",
    "context.get": "Leggo il contesto",
    "recall.get": "Leggo il ripasso",
    "artifact.get": "Leggo gli artefatti",
    "assessment.get": "Leggo la valutazione",
    "evidence.get": "Leggo le evidenze",
    "conversation.search": "Cerco nella conversazione",
    "conversation.read": "Leggo la conversazione",
    "study_memory.record": "Aggiorno la memoria di studio",
    "study_memory.search": "Cerco nella memoria di studio",
}


class _ObservedToolExecutor:
    """Record one source search without forwarding its query or evidence."""

    name = "source.search"
    behavior_version = BoundSourceSearchExecutor.behavior_version

    def __init__(self, inner: BoundSourceSearchExecutor, *, target: str, ref: str) -> None:
        self._inner = inner
        self._target = target
        self._ref = ref

    async def invoke(self, arguments: JsonObject) -> JsonObject:
        token = None
        with suppress(TypeError, ValueError):
            token = begin_activity(kind="retrieval", ref=self._ref, target=self._target)
        try:
            output = await self._inner.invoke(arguments)
