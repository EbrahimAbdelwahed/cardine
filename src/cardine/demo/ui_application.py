"""Versioned, repository-backed application surface for the Cardine UI.

The browser only translates HTTP envelopes.  All product state and writes are
owned by the canonical repository services or by their shared harness surface.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Protocol, cast

from cardine.cli.repository import (
    LocalRepository,
    LocalRepositoryError,
    ModelAdapterConfigurationError,
    ModelAdapterRegistry,
)
from cardine.courses import ProjectionCourseView
from cardine.diagnostics import TurnTraceStore
from cardine.documents import (
    AnyDocErrorCode,
    AnyDocWorkerError,
    DocumentImportPolicy,
    PdfAdmissionError,
    admit_pdf,
    document_import_policy,
)
from cardine.hosts import PendingContinuationDescriptor, TutorContinuationRecord
from cardine.integrations.study_agent import (
    CardineRuntimeConfig,
    StudyRuntimeAdapter,
    compose_study_runtime,
)
from cardine.integrations.study_agent.course_policy import (
    ConsentCommandError,
    ConsentConflictError,
    ConsentReceipt,
    ProviderConsentRequiredError,
    RetryableConsentConflictError,
    RetryableSourceLifetimeConflictError,
    SourceLifetimeCommandError,
)
from cardine.knowledge import LessonCandidate, SourcePin
from study_agent.application import (
    ConversationTurnCommand,
    ConversationTurnError,
    ConversationTurnErrorCode,
    GroundingAskError,
    StudyReadinessSnapshot,
)
from study_agent.artifacts import (
    ArtifactBatchRecord,
    ArtifactCommandError,
    ArtifactConflictError,
    ArtifactRevisionRecord,
    ArtifactSnapshot,
    GeneratedArtifactProvenance,
    ProjectionArtifactView,
    RetryableArtifactConflictError,
)
from study_agent.artifacts.content import AssessmentItemContent
from study_agent.artifacts.events import decision_command_fingerprint
from study_agent.assessments import (
    AssessmentCommandError,
    AssessmentConflictError,
    AssessmentSnapshot,
    AttemptRecord,
    FreeResponse,
    GradeContestRecord,
    GradeRecord,
    MultipleChoiceResponse,
    ProjectionAssessmentView,
    ProjectionLearnerEvidenceView,
    RetryableAssessmentConflictError,
    SingleChoiceResponse,
)
from study_agent.domain import (
    ArtifactDecision,
    ArtifactRevisionId,
    ArtifactRevisionStatus,
    AssessmentFormat,
    AttemptId,
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    GradeId,
    GradeLifecycle,
    PresentationId,
    PrincipalKind,
    SessionId,
    SourceId,
    StatementId,
    StudyArtifactKind,
    StudyContextSnapshot,
    StudyStatementKind,
    TutorSnapshotV1,
    artifact_event_id_for,
    recall_event_id_for,
    review_id_for,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.domain.identifiers import Identifier
from study_agent.ingestion import TextIngestionError
from study_agent.ports import (
    CourseNotFoundError,
    ModelError,
    ModelErrorCode,
    SessionNotFoundError,
)
from study_agent.ports.clock import ClockPort
from study_agent.ports.scheduling import SchedulingPolicyPort
from study_agent.recall import (
    DueRecallView,
    ProjectionRecallView,
    RecallCommandError,
    RecallConflictError,
    RecallRating,
    RecallSnapshot,
    RetryableRecallConflictError,
)
from study_agent.recall.events import REVIEW_RECORDED, SCHEDULE_APPLIED
from study_agent.retrieval import SourceContentError
from study_agent.sessions import ProjectionTutorPresentationView
from study_agent.sessions.events import grounded_answer_manifest
from study_agent.state import PayloadValidationError, Projection
from study_agent.study_context import (
    ProjectionStudyContextView,
    RetryableStudyContextConflictError,
    StudyContextCommandError,
    StudyContextConflictError,
)

from .product_shell import MAX_LEARNER_ENTRY_CHARS

# Recall cards are presentation DTOs, not a second content store.  Keep the
# response bounded even if a valid canonical artifact contains unusually long
# learner text; the browser never receives the omitted suffix.
MAX_RECALL_FRONT_CHARS = 800
MAX_RECALL_BACK_CHARS = 1800
MAX_RECALL_PROVENANCE_ITEMS = 8
MAX_WORKSPACE_ID_CHARS = 160
MAX_WORKSPACE_TITLE_CHARS = 240
MAX_WORKSPACE_LANGUAGE_CHARS = 32
MAX_WORKSPACE_GOALS = 12
MAX_WORKSPACE_GOAL_CHARS = 240
MAX_SOURCE_UPLOAD_BYTES = 196_608
MAX_SOURCE_FILENAME_CHARS = 240
MAX_SOURCE_TITLE_CHARS = 240

_REPOSITORY_LOCKS_GUARD = Lock()
_REPOSITORY_MUTATION_LOCKS: dict[Path, Lock] = {}


class UiApplicationPort(Protocol):
    """Small transport-independent boundary consumed by ``BrowserSurface``."""

    mode: str

    def get(self, path: str) -> JsonObject: ...

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject: ...


class _HarnessToolSurface(Protocol):
    async def invoke(
        self, name: str, arguments: JsonObject, context: ExecutionContext
    ) -> object: ...


class _HarnessToolRepository(Protocol):
    def harness_tools(self) -> _HarnessToolSurface: ...


def _harness_tools(repository: LocalRepository) -> _HarnessToolSurface:
    """Expose the repository's product surface with a typed local boundary."""

    return cast(_HarnessToolRepository, repository).harness_tools()


class UiRequestError(ValueError):
    """A versioned UI request cannot be served by this composition."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int = 400,
        diagnostic_code: str | None = None,
        command_committed: bool = False,
        request_id: str | None = None,
        trace_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.diagnostic_code = diagnostic_code
        self.command_committed = command_committed
        self.request_id = request_id
        self.trace_id = trace_id


def _source_grounding_status(
    repository: LocalRepository, course_id: CourseId, snapshot: TutorSnapshotV1
) -> JsonObject:
    """Expose grounding only when every displayed source has readable text."""

    retired = repository.source_lifetime.retired_source_ids(course_id)
    active_materials = tuple(item for item in snapshot.materials if item.source_id not in retired)
    expected_chunks = sum(item.chunk_count for item in active_materials)
    if expected_chunks == 0:
        return {"status": "empty", "indexed_chunks": 0}
    try:
        documents = tuple(
            item
            for item in repository.for_course(course_id).content.documents()
            if item.source_id not in retired
        )
    except (SourceContentError, OSError, ValueError):
        return {"status": "unavailable", "indexed_chunks": 0}
    if len(documents) != expected_chunks:
        return {"status": "unavailable", "indexed_chunks": len(documents)}
    return {"status": "available", "indexed_chunks": len(documents)}


def _flashcard_capability_available(
    repository: LocalRepository, course_id: CourseId, session_id: SessionId
) -> bool:
    """Report the capability only after the repository composes its owner."""

    try:
        repository.tutor_conversation(course_id, session_id=session_id)
    except (LocalRepositoryError, OSError, RuntimeError, TypeError, ValueError):
        return False
    return repository.flashcard_composition is not None


class RepositoryUiApplication(UiApplicationPort):
    """Compose one explicit local repository per request.

    The browser transport never receives a repository, provider, or event-store
    object.  This application owns only the selected identifiers and injected
    technical adapter configuration, then closes every repository it opens.
    """

    mode = "local_repository"

    def __init__(
        self,
        repository: str | Path,
        course_id: str | CourseId,
        session_id: str | SessionId,
        *,
        model_adapters: ModelAdapterRegistry | None = None,
        environment: Mapping[str, str] | None = None,
        recall_scheduler: SchedulingPolicyPort | None = None,
        recall_scheduler_factory: Callable[[], SchedulingPolicyPort] | None = None,
        repository_opener: Callable[..., AbstractContextManager[LocalRepository]] = (
            LocalRepository.open
        ),
        turn_traces: TurnTraceStore | None = None,
        document_policy: DocumentImportPolicy | None = None,
    ) -> None:
        self._repository = Path(repository)
        self._course_id = _identifier(course_id, CourseId, "course_id")
        self._session_id = _identifier(session_id, SessionId, "session_id")

        def open_repository() -> AbstractContextManager[LocalRepository]:
            kwargs: dict[str, object] = {
                "model_adapters": model_adapters,
                "environment": environment,
            }
            if recall_scheduler is not None:
                kwargs["recall_scheduler"] = recall_scheduler
            if recall_scheduler_factory is not None:
                kwargs["recall_scheduler_factory"] = recall_scheduler_factory
            return repository_opener(self._repository, **kwargs)

        self._runtime: StudyRuntimeAdapter[object, LocalRepository] = compose_study_runtime(
            CardineRuntimeConfig(opener=open_repository)
        )
        self._lock = _repository_mutation_lock(self._repository)
        self._turn_traces = turn_traces if turn_traces is not None else TurnTraceStore()
        self._document_policy = document_policy or document_import_policy()

    @property
    def repository(self) -> Path:
        return self._repository

    @property
    def course_id(self) -> CourseId:
        return self._course_id

    @property
    def session_id(self) -> SessionId:
        return self._session_id

    @property
    def turn_traces(self) -> TurnTraceStore:
        return self._turn_traces

    @property
    def document_policy(self) -> DocumentImportPolicy:
        return self._document_policy

    def _workspace(self) -> JsonObject:
        with self._lock, self._open() as repository:
            courses: list[JsonObject] = []
            for profile in repository.course_catalog.list_courses():
                sessions = tuple(
                    _workspace_session(item, selected=item.id == self._session_id)
                    for item in repository.sessions.list_sessions(profile.id)
                )
                courses.append(
                    {
                        "id": str(profile.id),
                        "title": profile.title,
                        "language": profile.language,
                        "exam_date": (
                            None if profile.exam_date is None else profile.exam_date.isoformat()
                        ),
                        "learning_goals": profile.learning_goals,
                        "assessment_styles": profile.assessment_styles,
                        "selected": profile.id == self._course_id,
                        "sessions": sessions,
                    }
                )
            return {
                "schema_version": 1,
                "selected": {
                    "course_id": str(self._course_id),
                    "session_id": str(self._session_id),
                },
                "courses": tuple(courses),
            }

    def get(self, path: str) -> JsonObject:
        if path == "/api/v1/workspace":
            return self._workspace()
        routes: dict[str, Callable[[TutorSnapshotV1, Mapping[str, object]], JsonObject]] = {
            "/api/v1/bootstrap": self._bootstrap,
            "/api/v1/session": self._session,
            "/api/v1/materials": self._materials,
            "/api/v1/artifacts": self._artifacts,
            "/api/v1/assessments": self._assessments,
            "/api/v1/evidence": self._evidence,
            "/api/v1/recall/due": self._recall,
            "/api/v1/context/conflicts": self._conflicts,
            "/api/v1/plan": self._plan,
            "/api/v1/consent": self._consent,
        }
        route = routes.get(path)
        if route is None:
            raise UiRequestError("route not found", status_code=404)
        try:
            with self._lock, self._open() as repository:
                readiness_projection, snapshot = self._captured_state(repository)

                def captured(_course_id: CourseId) -> Projection:
                    return readiness_projection

                profile = ProjectionCourseView(captured).get(self._course_id)
                artifacts = ProjectionArtifactView(captured).get(self._course_id)
                assessment = ProjectionAssessmentView(captured).get(self._course_id)
                evidence = ProjectionLearnerEvidenceView(ProjectionAssessmentView(captured)).get(
                    self._course_id
                )
                context = ProjectionStudyContextView(captured).get(self._course_id)
                recall_snapshot = ProjectionRecallView(captured).get(self._course_id)
                recall_projection = readiness_projection if path == "/api/v1/recall/due" else None
                readiness = repository.study_readiness.from_projection(
                    readiness_projection,
                    repository.clock,
                    recall_available=repository.recall_availability.available,
                ).get()
                presentations = ProjectionTutorPresentationView(captured).presentations(
                    self._course_id, self._session_id
                )
                return route(
                    snapshot,
                    {
                        "course_title": profile.title,
                        "readiness": readiness,
                        "artifacts": artifacts,
                        "recall_snapshot": recall_snapshot,
                        "recall_composition": repository.recall_composition,
                        "recall_payload": _recall_payload(
                            snapshot,
                            repository.recall_composition,
                            artifacts,
                            projection=recall_projection,
                            clock=repository.clock,
                        ),
                        "assessment": assessment,
                        "evidence": evidence,
                        "assessments": _assessment_payload(
                            assessment,
                            artifacts,
                            session_id=self._session_id,
                        ),
                        "context": context,
                        "presentations": presentations,
                        "source_grounding": _source_grounding_status(
                            repository, self._course_id, snapshot
                        ),
                        "pageindex": repository.pageindex_summary(self._course_id),
                        "provider_consent": repository.provider_consent.get(self._course_id),
                        "retired_source_ids": repository.source_lifetime.retired_source_ids(
                            self._course_id
                        ),
                        "flashcards_available": _flashcard_capability_available(
                            repository, self._course_id, self._session_id
                        ),
                        "continuation": _active_continuation(
                            repository,
                            self._course_id,
                            self._session_id,
                            presentations,
                        ),
                    },
                )
        except UiRequestError:
            raise
        except (PayloadValidationError, SourceContentError):
            if path == "/api/v1/materials":
                return {
                    "schema_version": 1,
                    "status": "unavailable",
                    "high_water_sequence": 0,
                    "items": (),
                    "message": (
                        "Il testo della fonte non è recuperabile in sicurezza: "
                        "lo studio guidato dalle fonti non è disponibile."
                    ),
                }
            raise UiRequestError(
                "canonical source text is unavailable; restore the source before continuing",
                status_code=503,
                diagnostic_code="source_content_unavailable",
            ) from None
        except (CourseNotFoundError, SessionNotFoundError, FileNotFoundError) as error:
            raise UiRequestError(
                "selected course or session was not found", status_code=404
            ) from error
        except (LocalRepositoryError, OSError, ValueError, RuntimeError) as error:
            raise UiRequestError("repository runtime is unavailable", status_code=503) from error

    def post(self, path: str, command: Mapping[str, object]) -> JsonObject:
        if path == "/api/v1/workspace/select":
            return self._select_workspace(command)
        if path == "/api/v1/workspace/sessions":
            return self._start_workspace_session(command)
        if path == "/api/v1/workspace/courses":
            return self._create_workspace_course(command)
        if path == "/api/v1/chat/course-creation":
            return self._create_chat_course(command)
        if path == "/api/v1/sources/upload":
            return self._upload_source(command)
        if path in {"/api/v1/consent/grant", "/api/v1/consent/revoke"}:
            return self._consent_mutation(path.rsplit("/", 1)[-1], command)
        if path in {"/api/v1/sources/retire", "/api/v1/sources/restore"}:
            return self._source_lifetime_mutation(path.rsplit("/", 1)[-1], command)
        if path == "/api/v1/settings/model/check":
            return self._check_model(command)
        if path == "/api/v1/lessons/search":
            return self._lesson_search(command)
        if path == "/api/v1/lessons/select":
            return self._lesson_select(command)
        if path == "/api/v1/lessons/ask":
            return self._lesson_ask(command)
        continuation_fingerprint = _continuation_fingerprint(path)
        artifact_revision = _artifact_decision_target(path)
        recall_enrollment = _recall_enrollment_target(path)
        recall_review = _recall_review_target(path)
        assessment_presentation = _assessment_presentation_target(path)
        assessment_attempt = _assessment_attempt_target(path)
        assessment_grade = _assessment_grade_target(path)
        assessment_contest = _assessment_contest_target(path)
        context_kind = _context_resolution_kind(path)
        if (
            path != "/api/v1/session/turns"
            and continuation_fingerprint is None
            and artifact_revision is None
            and recall_enrollment is None
            and recall_review is None
            and assessment_presentation is None
            and assessment_attempt is None
            and assessment_grade is None
            and assessment_contest is None
            and context_kind is None
        ):
            raise UiRequestError("mutation is not available in repository mode", status_code=405)
        if artifact_revision is not None:
            return self._post_artifact_decision(artifact_revision, command)
        if recall_enrollment is not None:
            return self._post_recall_enrollment(recall_enrollment, command)
        if recall_review is not None:
            return self._post_recall_review(recall_review, command)
        if assessment_presentation is not None:
            return self._post_assessment_presentation(assessment_presentation, command)
        if assessment_attempt is not None:
            return self._post_assessment_attempt(assessment_attempt, command)
        if assessment_grade is not None:
            return self._post_assessment_grade(assessment_grade, command)
        if assessment_contest is not None:
            return self._post_assessment_contest(assessment_contest, command)
        if context_kind is not None:
            return self._post_context_resolution(context_kind, command)
        payload_key = "content" if continuation_fingerprint is None else "response"
        request_id, expected_sequence, payload = _command(command, payload_key=payload_key)
        content = _bounded_content(payload.get(payload_key))
        with self._turn_traces.capture(request_id, expected_sequence) as trace_id, self._lock:
            try:
                with self._open() as repository:
                    application = repository.tutor_conversation(
                        self._course_id, session_id=self._session_id
                    )
                    turn = ConversationTurnCommand(
                        content,
                        self._context(request_id, request_id),
                        expected_sequence,
                    )
                    result = asyncio.run(
                        application.turn(turn)
                        if continuation_fingerprint is None
                        else application.resume_continuation(continuation_fingerprint, turn)
                    )
                    projection, refreshed = self._captured_state(repository)

                    def captured(_course_id: CourseId) -> Projection:
                        return projection

                    session = self._session(
                        refreshed,
                        {
                            "course_title": ProjectionCourseView(captured)
                            .get(self._course_id)
                            .title,
                            "presentations": ProjectionTutorPresentationView(
                                captured
                            ).presentations(self._course_id, self._session_id),
                            "continuation": result.pending_continuation,
                        },
                    )
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "trace_id": trace_id,
                        "status": result.status.value,
                        "high_water_sequence": refreshed.high_water_sequence,
                        "result": session,
                        "presentation_id": (
                            None if result.presentation is None else str(result.presentation.id)
                        ),
                    }
            except UiRequestError:
                raise
            except ConversationTurnError as error:
                raise _conversation_ui_error(
                    error, request_id=request_id, trace_id=trace_id
                ) from error
            except (CourseNotFoundError, SessionNotFoundError, FileNotFoundError) as error:
                raise UiRequestError(
                    "selected course or session was not found",
                    status_code=404,
                    trace_id=trace_id,
                ) from error
            except ModelAdapterConfigurationError as error:
                raise UiRequestError(
                    "configured model credential is unavailable",
                    status_code=503,
                    diagnostic_code="tutor_configuration",
                    trace_id=trace_id,
                ) from error
            except (
                LocalRepositoryError,
                OSError,
                ValueError,
                RuntimeError,
            ) as error:
                raise UiRequestError(
                    "repository runtime is unavailable",
                    status_code=503,
                    trace_id=trace_id,
                ) from error

    def _lesson_search(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected, payload = _workspace_command(
            command, required_keys={"query"}
        )
        query = _workspace_text(payload.get("query"), "query", MAX_LEARNER_ENTRY_CHARS)
        try:
            with self._lock, self._open() as repository:
                result = repository.search_lessons(self._course_id, query)
                sequence = repository.events.projection(self._course_id).sequence
        except (CourseNotFoundError, FileNotFoundError) as error:
            raise UiRequestError("selected course was not found", status_code=404) from error
        except (LocalRepositoryError, OSError, RuntimeError) as error:
            raise UiRequestError("repository runtime is unavailable", status_code=503) from error
        except ValueError as error:
            raise UiRequestError(str(error), status_code=400) from error
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": result.disposition.value,
            "high_water_sequence": sequence,
            "candidates": tuple(_lesson_candidate_payload(item) for item in result.candidates),
        }

    def _lesson_select(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected, payload = _workspace_command(
            command, required_keys={"query", "candidate_id"}
        )
        query = _workspace_text(payload.get("query"), "query", MAX_LEARNER_ENTRY_CHARS)
        candidate_id = _workspace_text(
            payload.get("candidate_id"), "candidate_id", MAX_WORKSPACE_ID_CHARS
        )
        try:
            with self._lock, self._open() as repository:
                pin = repository.select_lesson(self._course_id, query, candidate_id)
                sequence = repository.events.projection(self._course_id).sequence
        except (CourseNotFoundError, FileNotFoundError) as error:
            raise UiRequestError("selected course was not found", status_code=404) from error
        except (LocalRepositoryError, OSError, RuntimeError) as error:
            raise UiRequestError("repository runtime is unavailable", status_code=503) from error
        except ValueError as error:
            raise UiRequestError(str(error), status_code=400) from error
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": "selected",
            "high_water_sequence": sequence,
            "pin": _lesson_pin_payload(pin),
        }

    def _lesson_ask(self, command: Mapping[str, object]) -> JsonObject:
        request_id, expected_sequence, payload = _workspace_command(
            command, required_keys={"question", "pin"}
        )
        question = _workspace_text(
            payload.get("question"), "question", MAX_LEARNER_ENTRY_CHARS
        )
        pin = _lesson_pin_payload_from_json(payload.get("pin"))
        with self._lock:
            try:
                with self._open() as repository:
                    receipt = repository.rebuild_retrieval()
                    service = repository.grounding_service(
                        self._course_id,
                        repository.course_index_receipt(self._course_id, receipt),
                        lesson_pin=pin,
                    )
                    result = asyncio.run(
                        service.ask(
                            question,
                            self._context(request_id, request_id),
                            expected_sequence=expected_sequence,
                        )
                    )
                    sequence = repository.events.projection(self._course_id).sequence
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "completed",
                        "high_water_sequence": sequence,
                        "pin": _lesson_pin_payload(pin),
                        "answer": grounded_answer_manifest(result.answer.answer),
                        "answer_id": str(result.answer.id),
                        "run_id": str(result.answer.run_id),
                    }
            except GroundingAskError as error:
                status = 409 if error.code.value == "retryable_conflict" else 400
                raise UiRequestError(str(error), status_code=status) from error
            except ProviderConsentRequiredError as error:
                raise UiRequestError(
                    "provider consent is required before tutor execution", status_code=403
                ) from error
            except ModelAdapterConfigurationError as error:
                raise UiRequestError(
                    "configured model credential is unavailable", status_code=503
                ) from error
            except (CourseNotFoundError, SessionNotFoundError, FileNotFoundError) as error:
                raise UiRequestError(
                    "selected course or session was not found", status_code=404
                ) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error
            except ValueError as error:
                raise UiRequestError(str(error), status_code=400) from error

    def _select_workspace(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected_sequence, payload = _workspace_command(
            command, required_keys={"course_id", "session_id"}
        )
        course_id = _workspace_identifier(payload.get("course_id"), CourseId, "course_id")
        session_id = _workspace_identifier(payload.get("session_id"), SessionId, "session_id")
        try:
            with self._lock, self._open() as repository:
                repository.courses.get(course_id)
                repository.sessions.get_session(course_id, session_id)
        except (CourseNotFoundError, SessionNotFoundError) as error:
            raise UiRequestError(
                "selected course or session was not found", status_code=404
            ) from error
        # Commit the coupled selection only after both canonical owners were
        # read successfully.  A failed switch must leave the visible pair
        # untouched rather than combining the old course with a new session.
        self._course_id = course_id
        self._session_id = session_id
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": "selected",
            "selected": {"course_id": str(course_id), "session_id": str(session_id)},
        }

    def _start_workspace_session(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected_sequence, payload = _workspace_command(
            command, required_keys={"course_id", "session_id"}
        )
        course_id = _workspace_identifier(payload.get("course_id"), CourseId, "course_id")
        session_id = _workspace_identifier(payload.get("session_id"), SessionId, "session_id")
        with self._lock, self._open() as repository:
            session = repository.session_service.start(
                _workspace_context(request_id, course_id, session_id)
            )
            self._course_id = course_id
            self._session_id = session_id
            sequence = repository.events.projection(course_id).sequence
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": "started",
            "high_water_sequence": sequence,
            "selected": {"course_id": str(course_id), "session_id": str(session_id)},
            "session": _workspace_session(session, selected=True),
        }

    def _create_workspace_course(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected_sequence, payload = _workspace_command(
            command,
            required_keys={"course_id", "title", "language", "learning_goals"},
            optional_keys={"assessment_styles"},
        )
        course_id = _workspace_identifier(payload.get("course_id"), CourseId, "course_id")
        title = _workspace_text(payload.get("title"), "title", MAX_WORKSPACE_TITLE_CHARS)
        language = _workspace_text(
            payload.get("language"), "language", MAX_WORKSPACE_LANGUAGE_CHARS
        )
        learning_goals = _workspace_text_list(
            payload.get("learning_goals"),
            "learning_goals",
            MAX_WORKSPACE_GOALS,
            MAX_WORKSPACE_GOAL_CHARS,
        )
        assessment_styles = _workspace_text_list(
            payload.get("assessment_styles", ()),
            "assessment_styles",
            MAX_WORKSPACE_GOALS,
            MAX_WORKSPACE_GOAL_CHARS,
            allow_empty=True,
        )
        _ = CourseProfile(
            course_id,
            title,
            language,
            learning_goals=learning_goals,
            assessment_styles=assessment_styles,
        )
        with self._lock, self._open() as repository:
            result = asyncio.run(
                _harness_tools(repository).invoke(
                    "course.create",
                    {
                        "course_id": str(course_id),
                        "title": title,
                        "language": language,
                        "learning_goals": learning_goals,
                        "assessment_styles": assessment_styles,
                    },
                    _workspace_context(request_id, course_id),
                )
            )
            created = _surface_profile(result)
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": "created",
            "course": {
                "id": str(created.id),
                "title": created.title,
                "language": created.language,
                "learning_goals": created.learning_goals,
                "assessment_styles": created.assessment_styles,
            },
        }

    def _create_chat_course(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected_sequence, payload = _workspace_command(
            command,
            required_keys={
                "confirmed",
                "course_id",
                "title",
                "language",
                "learning_goals",
                "session_id",
            },
            optional_keys={"assessment_styles"},
        )
        if payload.get("confirmed") is not True:
            raise UiRequestError("course creation requires explicit confirmation")
        course_id = _workspace_identifier(payload.get("course_id"), CourseId, "course_id")
        session_id = _workspace_identifier(payload.get("session_id"), SessionId, "session_id")
        title = _workspace_text(payload.get("title"), "title", MAX_WORKSPACE_TITLE_CHARS)
        language = _workspace_text(
            payload.get("language"), "language", MAX_WORKSPACE_LANGUAGE_CHARS
        )
        learning_goals = _workspace_text_list(
            payload.get("learning_goals"),
            "learning_goals",
            MAX_WORKSPACE_GOALS,
            MAX_WORKSPACE_GOAL_CHARS,
        )
        assessment_styles = _workspace_text_list(
            payload.get("assessment_styles", ()),
            "assessment_styles",
            MAX_WORKSPACE_GOALS,
            MAX_WORKSPACE_GOAL_CHARS,
            allow_empty=True,
        )
        _ = CourseProfile(
            course_id,
            title,
            language,
            learning_goals=learning_goals,
            assessment_styles=assessment_styles,
        )
        with self._lock, self._open() as repository:
            course_result = asyncio.run(
                _harness_tools(repository).invoke(
                    "course.create",
                    {
                        "course_id": str(course_id),
                        "title": title,
                        "language": language,
                        "learning_goals": learning_goals,
                        "assessment_styles": assessment_styles,
                    },
                    _workspace_context(f"{request_id}-course", course_id),
                )
            )
            created = _surface_profile(course_result)
            session_result = asyncio.run(
                _harness_tools(repository).invoke(
                    "session.start",
                    {"session_id": str(session_id)},
                    _workspace_context(f"{request_id}-session", course_id, session_id),
                )
            )
            _surface_success(session_result)
            session = repository.sessions.get_session(course_id, session_id)
            self._course_id = course_id
            self._session_id = session_id
            sequence = repository.events.projection(course_id).sequence
        return {
            "schema_version": 1,
            "request_id": request_id,
            "status": "created",
            "high_water_sequence": sequence,
            "selected": {"course_id": str(course_id), "session_id": str(session_id)},
            "course": {
                "id": str(created.id),
                "title": created.title,
                "language": created.language,
                "learning_goals": created.learning_goals,
                "assessment_styles": created.assessment_styles,
            },
            "session": _workspace_session(session, selected=True),
        }

    def _upload_source(self, command: Mapping[str, object]) -> JsonObject:
        """Ingest a bounded UTF-8 text revision through the canonical service."""

        request_id, _expected_sequence, payload = _workspace_command(
            command, required_keys={"filename", "title", "content"}
        )
        filename = _workspace_text(payload.get("filename"), "filename", MAX_SOURCE_FILENAME_CHARS)
        title = _workspace_text(payload.get("title"), "title", MAX_SOURCE_TITLE_CHARS)
        content = _source_upload_content(payload.get("content"))
        with self._lock:
            try:
                with self._open() as repository:
                    surface_result = asyncio.run(
                        _harness_tools(repository).invoke(
                            "source.ingest",
                            {"filename": filename, "title": title, "content": content},
                            ExecutionContext(
                                PrincipalKind.HUMAN,
                                "study-agent-source-upload",
                                self._course_id,
                                CorrelationId(f"cardine-source-upload-{request_id}"),
                                frozenset({"source:write"}),
                                self._session_id,
                                idempotency_key=request_id,
                            ),
                        )
                    )
                    result = _surface_value(surface_result)
                    repository.rebuild_retrieval()
                    repository.reconcile_pageindex(self._course_id)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": result["status"],
                        "high_water_sequence": result["high_water_sequence"],
                        "source": {
                            "source_id": result["source_id"],
                            "revision_id": result["revision_id"],
                            "title": result["title"],
                            "kind": "text",
                            "chunk_count": result["chunk_count"],
                        },
                    }
            except UiRequestError:
                raise
            except (CourseNotFoundError, SessionNotFoundError, FileNotFoundError) as error:
                raise UiRequestError(
                    "selected course or session was not found", status_code=404
                ) from error
            except (LocalRepositoryError, OSError, ValueError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def import_pdf(
        self,
        *,
        input_path: Path,
        pdf_sha256: str,
        byte_size: int,
        filename: str,
        title: str,
        request_id: str,
    ) -> JsonObject:
        """Convert a streamed PDF and commit one extracted Markdown revision."""

        filename = _workspace_text(filename, "filename", MAX_SOURCE_FILENAME_CHARS)
        title = _workspace_text(title, "title", MAX_SOURCE_TITLE_CHARS)
        request_id = _workspace_text(request_id, "request_id", 200)
        if not filename.lower().endswith(".pdf") or "/" in filename or "\\" in filename:
            raise UiRequestError("only .pdf files are supported", status_code=415)
        if (
            type(byte_size) is not int
            or not 0 < byte_size <= self._document_policy.max_document_bytes
        ):
            raise UiRequestError("PDF size is invalid", status_code=413)
        source_id = SourceId("source-pdf-sha256:" + pdf_sha256)
        with self._lock:
            try:
                with self._open() as repository:
                    admission = admit_pdf(
                        input_path=input_path,
                        expected_sha256=pdf_sha256,
                        byte_size=byte_size,
                        filename=filename,
                        source_id=source_id,
                        title=title,
                        trust_level=80,
                        source_role="learner_uploaded",
                        context=ExecutionContext(
                            PrincipalKind.HUMAN,
                            "cardine-pdf-upload",
                            self._course_id,
                            CorrelationId("cardine-pdf-upload-" + request_id),
                            frozenset({"source:write"}),
                            self._session_id,
                            idempotency_key=request_id,
                        ),
                        ingestion=repository.for_course(self._course_id).ingestion,
                        policy=self._document_policy,
                    )
                    repository.rebuild_retrieval()
                    result = admission.result
                    conversion = admission.conversion
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": result.status.value,
                        "high_water_sequence": result.committed_sequence,
                        "source": {
                            "source_id": str(result.source.source_id),
                            "revision_id": str(result.source.revision_id),
                            "title": result.source.title,
                            "kind": "pdf",
                            "chunk_count": len(result.chunks),
                        },
                        "conversion": {
                            "adapter": "pdf-to-markdown-anydoc@1",
                            "version": conversion.anydoc_version,
                            "page_count": conversion.page_count,
                            "page_map_fingerprint": admission.provenance.fingerprint,
                            "pdf_sha256": conversion.pdf_sha256,
                            "markdown_sha256": conversion.markdown_sha256,
                            "limitations": conversion.limitations,
                        },
                    }
            except AnyDocWorkerError as error:
                status = {
                    AnyDocErrorCode.UNSUPPORTED.value: 415,
                    AnyDocErrorCode.RESOURCE_LIMIT.value: 413,
                    AnyDocErrorCode.OUTPUT_LIMIT.value: 413,
                    AnyDocErrorCode.WORKER_TIMEOUT.value: 504,
                    AnyDocErrorCode.WORKER_UNAVAILABLE.value: 503,
                }.get(error.code, 422)
                raise UiRequestError(
                    "PDF conversion failed safely; no source was admitted",
                    status_code=status,
                    diagnostic_code=error.code,
                ) from None
            except PdfAdmissionError as error:
                raise UiRequestError(str(error), status_code=409) from None
            except TextIngestionError as error:
                raise UiRequestError(
                    "converted PDF could not be admitted canonically",
                    status_code=409 if error.retryable else 422,
                ) from error

    def _check_model(self, command: Mapping[str, object]) -> JsonObject:
        request_id, _expected_sequence, payload = _workspace_command(command, required_keys=set())
        if payload:
            raise UiRequestError("model check payload must be empty")
        try:
            with self._lock, self._open() as repository:
                if repository.config.model is None:
                    raise ModelAdapterConfigurationError("no model adapter is configured")
                application = repository.tutor_conversation(
                    self._course_id, session_id=self._session_id
                )

                asyncio.run(application.verify_model_readiness(self._course_id, self._session_id))
                return {
                    "schema_version": 1,
                    "request_id": request_id,
                    "status": "ok",
                    "adapter_id": repository.config.model.adapter_id,
                    "model": repository.config.model.adapter_id,
                    "scope": "tutor_decision",
                }
        except ModelError as error:
            reason = _model_check_reason(error.code.value)
            return {
                "schema_version": 1,
                "request_id": request_id,
                "status": "error",
                "reason": reason,
                "message": _model_check_message(reason),
            }
        except ModelAdapterConfigurationError:
            return {
                "schema_version": 1,
                "request_id": request_id,
                "status": "error",
                "reason": "configuration",
                "message": "Configura una chiave API valida per il modello selezionato.",
            }
        except (OSError, RuntimeError, ValueError) as error:
            reason = _model_check_reason(getattr(error, "failure_reason", None))
            return {
                "schema_version": 1,
                "request_id": request_id,
                "status": "error",
                "reason": reason,
                "message": _model_check_message(reason),
            }

    def _post_artifact_decision(
        self, revision_id: str, command: Mapping[str, object]
    ) -> JsonObject:
        request_id, expected_sequence, payload = _command(command, payload_key="decision")
        decision_raw = payload.get("decision")
        decision = {
            "accepted": ArtifactDecision.ACCEPT,
            "rejected": ArtifactDecision.REJECT,
            "accept": ArtifactDecision.ACCEPT,
            "reject": ArtifactDecision.REJECT,
        }.get(decision_raw if isinstance(decision_raw, str) else "")
        if decision is None:
            raise UiRequestError("artifact decision is invalid")
        with self._lock:
            try:
                with self._open() as repository:
                    artifact = repository.artifacts.get(self._course_id)
                    target = artifact.revision(ArtifactRevisionId(revision_id))
                    current = next(
                        (
                            item
                            for item in artifact.history(target.artifact_id)
                            if item.status.value == "accepted"
                        ),
                        None,
                    )
                    supersedes = (
                        None
                        if decision is ArtifactDecision.REJECT or current is None
                        else current.id
                    )
                    existing_fingerprint = repository.artifacts.command_fingerprint(
                        self._course_id,
                        artifact_event_id_for(
                            self._course_id, self._session_id, request_id, "decision"
                        ),
                    )
                    if existing_fingerprint is not None:
                        supersedes = _retry_supersedes_revision(
                            target.id,
                            decision,
                            artifact.history(target.artifact_id),
                            existing_fingerprint,
                        )
                    repository.artifact_service.record_human_decision(
                        target.id,
                        decision,
                        supersedes,
                        self._context(request_id, request_id),
                        expected_sequence,
                    )
                    projection, _snapshot = self._captured_state(repository)

                    def captured(_course_id: CourseId) -> Projection:
                        return projection

                    artifacts = ProjectionArtifactView(captured).get(self._course_id)
                    recall_snapshot = ProjectionRecallView(captured).get(self._course_id)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": projection.sequence,
                        "result": _artifact_payload(
                            artifacts,
                            self._session_id,
                            recall_snapshot=recall_snapshot,
                            recall_availability=repository.recall_availability,
                        ),
                    }
            except (ArtifactCommandError, ArtifactConflictError) as error:
                raise UiRequestError(
                    "artifact decision conflicts with canonical state", status_code=409
                ) from error
            except RetryableArtifactConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except (LookupError, ValueError) as error:
                raise UiRequestError("artifact decision is invalid", status_code=400) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _consent(self, snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        receipt = cast(ConsentReceipt | None, metadata.get("provider_consent"))
        return {
            "schema_version": 1,
            "course_id": str(snapshot.course_id),
            "granted": bool(getattr(receipt, "granted", False)),
            "receipt": None
            if receipt is None
            else {
                "status": receipt.status,
                "request_id": receipt.request_id,
                "sequence": receipt.sequence,
            },
        }

    def _consent_mutation(self, action: str, command: Mapping[str, object]) -> JsonObject:
        request_id, expected, payload = _workspace_command(command, required_keys=set())
        if set(payload) - {"request_id"}:
            raise UiRequestError("consent payload contains unknown fields")
        request_value = _workspace_text(payload.get("request_id", request_id), "request_id", 160)
        try:
            with self._lock, self._open() as repository:
                method = (
                    repository.provider_consent_service.grant
                    if action == "grant"
                    else repository.provider_consent_service.revoke
                )
                receipt = method(
                    self._course_policy_context(request_id),
                    request_value,
                    expected_sequence=expected,
                )
                return {
                    "schema_version": 1,
                    "request_id": request_id,
                    "status": receipt.status,
                    "high_water_sequence": receipt.sequence,
                }
        except RetryableConsentConflictError as error:
            raise UiRequestError("expected sequence is stale", status_code=409) from error
        except (ConsentCommandError, ConsentConflictError) as error:
            raise UiRequestError(
                "consent command conflicts with policy", status_code=409
            ) from error

    def _source_lifetime_mutation(self, action: str, command: Mapping[str, object]) -> JsonObject:
        request_id, expected, payload = _workspace_command(command, required_keys={"source_id"})
        source_id = _workspace_identifier(payload.get("source_id"), SourceId, "source_id")
        committed = False
        try:
            with self._lock, self._open() as repository:
                method = (
                    repository.source_lifetime_service.retire
                    if action == "retire"
                    else repository.source_lifetime_service.restore
                )
                receipt = method(
                    self._course_policy_context(request_id),
                    source_id,
                    request_id,
                    expected_sequence=expected,
                )
                committed = True
                repository.rebuild_retrieval()
                return {
                    "schema_version": 1,
                    "request_id": request_id,
                    "source_id": str(source_id),
                    "status": receipt.status,
                    "high_water_sequence": receipt.sequence,
                }
        except RetryableSourceLifetimeConflictError as error:
            raise UiRequestError("expected sequence is stale", status_code=409) from error
        except SourceLifetimeCommandError as error:
            raise UiRequestError("source lifetime command is invalid", status_code=409) from error
        except (LocalRepositoryError, OSError, RuntimeError) as error:
            if committed:
                raise UiRequestError(
                    "source state committed but retrieval rebuild failed",
                    status_code=503,
                    command_committed=True,
                    request_id=request_id,
                ) from error
            raise

    def _post_recall_enrollment(
        self, revision_id: str, command: Mapping[str, object]
    ) -> JsonObject:
        request_id, expected_sequence, _payload = _command(command, payload_key=None)
        try:
            target = ArtifactRevisionId(revision_id)
        except ValueError as error:
            raise UiRequestError("recall enrollment target is invalid") from error
        with self._lock:
            try:
                with self._open() as repository:
                    repository.sessions.get_session(self._course_id, self._session_id)
                    composition = repository.recall_composition
                    if not composition.availability.available or composition.service is None:
                        raise UiRequestError(
                            composition.availability.message,
                            status_code=503,
                        )
                    projection = repository.events.projection(self._course_id)
                    enrollment_event_id = recall_event_id_for(
                        self._course_id,
                        self._session_id,
                        _enrollment_idempotency_key(self._course_id, self._session_id, target),
                        SCHEDULE_APPLIED,
                    )
                    retry = any(
                        event.event_id == enrollment_event_id
                        for event in repository.events.read(self._course_id)
                    )
                    if projection.sequence != expected_sequence and not retry:
                        raise UiRequestError("expected sequence is stale", status_code=409)
                    composition.service.enroll(
                        target,
                        self._recall_context(
                            request_id,
                            PrincipalKind.SERVICE,
                            "enrollment",
                            _enrollment_idempotency_key(self._course_id, self._session_id, target),
                        ),
                        expected_sequence,
                    )
                    projection, refreshed = self._captured_state(repository)

                    def captured(_course_id: CourseId) -> Projection:
                        return projection

                    artifacts = ProjectionArtifactView(captured).get(self._course_id)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": projection.sequence,
                        "result": _recall_payload(
                            refreshed,
                            composition,
                            artifacts,
                            projection=projection,
                            clock=repository.clock,
                        ),
                    }
            except UiRequestError:
                raise
            except RetryableRecallConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except RecallConflictError as error:
                raise UiRequestError(
                    "recall enrollment conflicts with canonical state", status_code=409
                ) from error
            except RecallCommandError as error:
                raise UiRequestError(
                    "recall enrollment is unavailable; acceptance remains committed",
                    status_code=503,
                ) from error
            except (CourseNotFoundError, SessionNotFoundError) as error:
                raise UiRequestError(
                    "selected course or session was not found", status_code=404
                ) from error
            except (LookupError, ValueError, TypeError) as error:
                raise UiRequestError("recall enrollment target is invalid") from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _post_recall_review(self, revision_id: str, command: Mapping[str, object]) -> JsonObject:
        request_id, expected_sequence, payload = _command(command, payload_key="rating")
        try:
            target = ArtifactRevisionId(revision_id)
            rating_raw = payload["rating"]
            if not isinstance(rating_raw, str):
                raise TypeError("recall rating must be text")
            rating = RecallRating(rating_raw)
        except (KeyError, TypeError, ValueError) as error:
            raise UiRequestError("recall rating is invalid") from error
        with self._lock:
            try:
                with self._open() as repository:
                    repository.sessions.get_session(self._course_id, self._session_id)
                    composition = repository.recall_composition
                    if not composition.availability.available or composition.service is None:
                        raise UiRequestError(
                            composition.availability.message,
                            status_code=503,
                        )
                    projection = repository.events.projection(self._course_id)
                    review_event_id = recall_event_id_for(
                        self._course_id,
                        self._session_id,
                        request_id,
                        REVIEW_RECORDED,
                    )
                    schedule_event_id = recall_event_id_for(
                        self._course_id,
                        self._session_id,
                        request_id,
                        SCHEDULE_APPLIED,
                    )
                    retry = any(
                        event.event_id in {review_event_id, schedule_event_id}
                        for event in repository.events.read(self._course_id)
                    )
                    if projection.sequence != expected_sequence and not retry:
                        raise UiRequestError("expected sequence is stale", status_code=409)
                    composition.service.review(
                        target,
                        rating,
                        self._recall_context(
                            request_id,
                            PrincipalKind.HUMAN,
                            "review",
                            request_id,
                        ),
                        expected_sequence,
                    )
                    projection, refreshed = self._captured_state(repository)

                    def captured(_course_id: CourseId) -> Projection:
                        return projection

                    recall_snapshot = ProjectionRecallView(captured).get(self._course_id)
                    next_schedule = _next_schedule_payload(
                        recall_snapshot, target, self._session_id, request_id
                    )
                    result_payload = dict(
                        _recall_payload(
                            refreshed,
                            composition,
                            ProjectionArtifactView(captured).get(self._course_id),
                            projection=projection,
                            clock=repository.clock,
                        )
                    )
                    result_payload["next_schedule"] = next_schedule
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": projection.sequence,
                        "next_schedule": next_schedule,
                        "result": result_payload,
                    }
            except UiRequestError:
                raise
            except RetryableRecallConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except RecallConflictError as error:
                raise UiRequestError(
                    "recall review conflicts with canonical state", status_code=409
                ) from error
            except RecallCommandError as error:
                raise UiRequestError("recall review is unavailable", status_code=409) from error
            except (CourseNotFoundError, SessionNotFoundError) as error:
                raise UiRequestError(
                    "selected course or session was not found", status_code=404
                ) from error
            except (LookupError, ValueError, TypeError) as error:
                raise UiRequestError("recall review target or rating is invalid") from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _post_assessment_presentation(
        self, revision_id: str, command: Mapping[str, object]
    ) -> JsonObject:
        request_id, expected_sequence, _payload = _command(command, payload_key=None)
        with self._lock:
            try:
                with self._open() as repository:
                    repository.assessment_service.present_item(
                        ArtifactRevisionId(revision_id),
                        self._assessment_context(
                            request_id, request_id, PrincipalKind.SERVICE, "present"
                        ),
                        expected_sequence,
                    )
                    sequence, payload_result = self._captured_assessment_payload(repository)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": sequence,
                        "result": payload_result,
                    }
            except RetryableAssessmentConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except (AssessmentConflictError, AssessmentCommandError) as error:
                raise UiRequestError(
                    "assessment presentation conflicts with canonical state", status_code=409
                ) from error
            except (LookupError, ValueError) as error:
                raise UiRequestError(
                    "assessment presentation is invalid", status_code=400
                ) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _post_assessment_attempt(
        self, presentation_id: str, command: Mapping[str, object]
    ) -> JsonObject:
        request_id, expected_sequence, payload = _command(command, payload_key="response")
        with self._lock:
            try:
                with self._open() as repository:
                    assessment = repository.assessments.get(self._course_id)
                    presentation = assessment.presentation(PresentationId(presentation_id))
                    response = _canonical_browser_response(
                        payload.get("response"),
                        presentation.content.format,
                        presentation.content.options,
                    )
                    repository.assessment_service.record_attempt(
                        presentation.id,
                        response,
                        None,
                        self._assessment_context(
                            request_id, request_id, PrincipalKind.HUMAN, "attempt"
                        ),
                        expected_sequence,
                    )
                    sequence, payload_result = self._captured_assessment_payload(repository)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": sequence,
                        "result": payload_result,
                    }
            except RetryableAssessmentConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except AssessmentConflictError as error:
                raise UiRequestError(
                    "assessment attempt conflicts with canonical state", status_code=409
                ) from error
            except AssessmentCommandError as error:
                raise UiRequestError(
                    "assessment attempt is unavailable", status_code=409
                ) from error
            except (LookupError, ValueError, TypeError) as error:
                raise UiRequestError("assessment response is invalid", status_code=400) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _post_assessment_grade(self, attempt_id: str, command: Mapping[str, object]) -> JsonObject:
        request_id, expected_sequence, payload = _command(
            command, payload_key=None, allow_empty_payload=True
        )
        supersedes = _optional_grade_id(payload)
        with self._lock:
            try:
                with self._open() as repository:
                    assessment = repository.assessments.get(self._course_id)
                    attempt = assessment.attempt(AttemptId(attempt_id))
                    presentation = assessment.presentation(attempt.presentation_id)
                    context = self._assessment_context(
                        request_id, request_id, PrincipalKind.SERVICE, "grade"
                    )
                    if presentation.content.format is AssessmentFormat.FREE_RESPONSE:
                        if assessment.sequence != expected_sequence:
                            raise RetryableAssessmentConflictError(
                                "course stream advanced before free-response grading"
                            )
                        if (
                            attempt.session_id != self._session_id
                            or presentation.session_id != self._session_id
                        ):
                            raise AssessmentCommandError(
                                "free-response grade target belongs to another session"
                            )
                        return {
                            "schema_version": 1,
                            "request_id": request_id,
                            "status": "needs_review",
                            "high_water_sequence": assessment.sequence,
                            "grade_id": None,
                            "result": {
                                "status": "needs_review",
                                "attempt_id": str(attempt.id),
                                "message": (
                                    "La valutazione delle risposte aperte richiede un "
                                    "responsabile verificato: nessun voto è stato registrato."
                                ),
                            },
                        }
                    result = repository.assessment_service.grade_closed(
                        attempt.id,
                        context,
                        expected_sequence,
                        supersedes_grade_id=supersedes,
                    )
                    sequence, payload_result = self._captured_assessment_payload(repository)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": sequence,
                        "result": payload_result,
                        "grade_id": str(result.id),
                    }
            except RetryableAssessmentConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except AssessmentConflictError as error:
                raise UiRequestError(
                    "assessment grade conflicts with canonical state", status_code=409
                ) from error
            except AssessmentCommandError as error:
                raise UiRequestError("assessment grade is unavailable", status_code=409) from error
            except (LookupError, ValueError, TypeError) as error:
                raise UiRequestError("assessment grade is invalid", status_code=400) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _post_assessment_contest(self, grade_id: str, command: Mapping[str, object]) -> JsonObject:
        request_id, expected_sequence, payload = _command(command, payload_key="reason")
        reason = payload.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise UiRequestError("contest reason is invalid")
        with self._lock:
            try:
                with self._open() as repository:
                    result = repository.assessment_service.contest_grade(
                        GradeId(grade_id),
                        _bounded_content(reason),
                        self._assessment_context(
                            request_id, request_id, PrincipalKind.HUMAN, "contest"
                        ),
                        expected_sequence,
                    )
                    sequence, payload_result = self._captured_assessment_payload(repository)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": sequence,
                        "result": payload_result,
                        "grade_id": str(result.grade_id),
                    }
            except RetryableAssessmentConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except (AssessmentConflictError, AssessmentCommandError) as error:
                raise UiRequestError(
                    "assessment contest conflicts with canonical state", status_code=409
                ) from error
            except (LookupError, ValueError, TypeError) as error:
                raise UiRequestError("assessment contest is invalid", status_code=400) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _post_context_resolution(self, kind_raw: str, command: Mapping[str, object]) -> JsonObject:
        request_id, expected_sequence, payload = _command(
            command, payload_key="selected_statement_id"
        )
        selected_raw = payload.get("selected_statement_id")
        if not isinstance(selected_raw, str) or not selected_raw.strip():
            raise UiRequestError("selected_statement_id is invalid")
        try:
            kind = StudyStatementKind(kind_raw)
        except ValueError as error:
            raise UiRequestError("context conflict kind is invalid") from error
        with self._lock:
            try:
                with self._open() as repository:
                    repository.study_context_service.resolve(
                        kind,
                        StatementId(selected_raw),
                        self._context(request_id, request_id),
                        expected_sequence,
                    )
                    projection, _snapshot = self._captured_state(repository)

                    def captured(_course_id: CourseId) -> Projection:
                        return projection

                    context = ProjectionStudyContextView(captured).get(self._course_id)
                    return {
                        "schema_version": 1,
                        "request_id": request_id,
                        "status": "committed",
                        "high_water_sequence": projection.sequence,
                        "result": _context_payload(context),
                    }
            except RetryableStudyContextConflictError as error:
                raise UiRequestError("expected sequence is stale", status_code=409) from error
            except (StudyContextConflictError, StudyContextCommandError) as error:
                raise UiRequestError(
                    "context resolution conflicts with canonical state", status_code=409
                ) from error
            except (LookupError, ValueError) as error:
                raise UiRequestError("context resolution is invalid", status_code=400) from error
            except (LocalRepositoryError, OSError, RuntimeError) as error:
                raise UiRequestError(
                    "repository runtime is unavailable", status_code=503
                ) from error

    def _open(self) -> AbstractContextManager[LocalRepository]:
        return self._runtime.open_repository()

    def _snapshot(self, repository: LocalRepository) -> TutorSnapshotV1:
        repository.courses.get(self._course_id)
        repository.sessions.get_session(self._course_id, self._session_id)
        return repository.tutor_snapshots.get(self._course_id, self._session_id)

    def _captured_state(self, repository: LocalRepository) -> tuple[Projection, TutorSnapshotV1]:
        """Capture one coherent event HWM for a complete response DTO."""

        for _attempt in range(3):
            projection = repository.events.projection(self._course_id)
            snapshot = self._snapshot(repository)
            if snapshot.high_water_sequence == projection.sequence:
                return projection, snapshot
        raise UiRequestError("repository changed during read; retry", status_code=409)

    def _captured_assessment_payload(self, repository: LocalRepository) -> tuple[int, JsonObject]:
        projection, snapshot = self._captured_state(repository)

        def captured(_course_id: CourseId) -> Projection:
            return projection

        return (
            projection.sequence,
            self._assessments(
                snapshot,
                {
                    "assessment": ProjectionAssessmentView(captured).get(self._course_id),
                    "artifacts": ProjectionArtifactView(captured).get(self._course_id),
                },
            ),
        )

    def _context(self, request_id: str, idempotency_key: str) -> ExecutionContext:
        return ExecutionContext(
            PrincipalKind.HUMAN,
            "study-agent-shell-web",
            self._course_id,
            CorrelationId(f"cardine-browser-{request_id}"),
            frozenset({"study:ask"}),
            self._session_id,
            idempotency_key=idempotency_key,
        )

    def _course_policy_context(self, request_id: str) -> ExecutionContext:
        return ExecutionContext(
            PrincipalKind.HUMAN,
            "study-agent-shell-web",
            self._course_id,
            CorrelationId(f"cardine-browser-policy-{request_id}"),
            idempotency_key=request_id,
        )

    def _recall_context(
        self,
        request_id: str,
        principal: PrincipalKind,
        domain: str,
        idempotency_key: str,
    ) -> ExecutionContext:
        return ExecutionContext(
            principal,
            f"study-agent-recall-{domain}",
            self._course_id,
            CorrelationId(f"cardine-recall-{domain}-{request_id}"),
            frozenset({"study:recall"}),
            self._session_id,
            idempotency_key=idempotency_key,
        )

    def _assessment_context(
        self,
        request_id: str,
        idempotency_key: str,
        principal: PrincipalKind,
        domain: str,
    ) -> ExecutionContext:
        return ExecutionContext(
            principal,
            f"study-agent-assessment-{domain}",
            self._course_id,
            CorrelationId(f"cardine-assessment-{domain}-{request_id}"),
            frozenset({"study:assessment"}),
            self._session_id,
            idempotency_key=idempotency_key,
        )

    @staticmethod
    def _bootstrap(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        readiness = cast(StudyReadinessSnapshot, metadata["readiness"])
        source_grounding = cast(Mapping[str, object], metadata["source_grounding"])
        pageindex = cast(Mapping[str, object], metadata.get("pageindex", {}))
        grounding_status = str(source_grounding.get("status", "unavailable"))
        artifact_counts = tuple(getattr(readiness, "artifact_counts", ()))
        recall = getattr(readiness, "recall", None)
        conflicted_kinds = {
            getattr(row, "kind", "")
            for row in tuple(getattr(readiness, "constraints", ()))
            if getattr(row, "status", "") == "conflicted"
        }
        due_count = getattr(recall, "due_count", None)
        pending_proposals = sum(int(getattr(item, "pending", 0)) for item in artifact_counts)
        shell_status = _readiness_shell_status(snapshot, readiness)
        readiness_payload = cast(JsonObject, readiness.to_json())
        assessment_count = sum(
            int(getattr(item, "accepted", 0))
            for item in artifact_counts
            if getattr(item, "kind", "") == StudyArtifactKind.ASSESSMENT_ITEM.value
        )
        consent = metadata.get("provider_consent")
        retired = {
            str(item) for item in cast(tuple[object, ...], metadata.get("retired_source_ids", ()))
        }
        active_materials = tuple(
            item for item in snapshot.materials if str(item.source_id) not in retired
        )
        active_revisions_value = pageindex.get("active_revisions", 0)
        active_revisions = (
            active_revisions_value if type(active_revisions_value) is int else 0
        )
        items_value = pageindex.get("items", ())
        pageindex_items = (
            tuple(item for item in items_value if isinstance(item, Mapping))
            if isinstance(items_value, (tuple, list))
            else ()
        )
        return {
            "schema_version": 1,
            "mode": "local_repository",
            "course": {"id": str(snapshot.course_id), "title": str(metadata["course_title"])},
            "session": {
                "id": str(snapshot.session_id),
                "status": snapshot.session_status.value,
            },
            "high_water_sequence": readiness.sequence,
            "shell_status": shell_status,
            "provider_consent": {
                "granted": bool(getattr(consent, "granted", False)),
                "status": getattr(consent, "status", "absent"),
            },
            "features": {
                "tutor": True,
                "artifacts": True,
                "flashcards": bool(metadata.get("flashcards_available", False)),
                "assessments": True,
                "evidence": True,
                "recall": bool(getattr(recall, "available", False)),
                "context_resolution": True,
                "exam_plan": True,
            },
            "counts": {
                "pending_proposals": pending_proposals,
                "assessments": assessment_count,
                "due_reviews": 0 if due_count is None else due_count,
                "context_conflicts": len(conflicted_kinds),
            },
            "materials": {
                "count": len(active_materials),
                "grounding_status": grounding_status,
                "items": tuple(
                    {
                        "title": item.title,
                        "kind": item.kind.value,
                        "chunk_count": item.chunk_count,
                    }
                    for item in active_materials
                ),
            },
            "pageindex": {
                "status": str(pageindex.get("status", "empty")),
                "active_revisions": active_revisions,
                "items": pageindex_items,
            },
            "onboarding": {
                "needs_study_intent": bool(active_materials)
                and grounding_status == "available"
                and not any(
                    getattr(getattr(item, "kind", None), "value", None) == "learner"
                    for item in snapshot.timeline
                ),
            },
            "readiness": readiness_payload,
        }

    @staticmethod
    def _session(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        presentations = metadata.get("presentations", ())
        readiness = metadata.get("readiness")
        canonical_timeline = [_timeline_item(item) for item in snapshot.timeline]
        if isinstance(presentations, Sequence):
            canonical_timeline.extend(
                _presentation_timeline_item(item)
                for item in presentations
                if hasattr(item, "course_sequence")
            )
        canonical_timeline.sort(key=lambda item: int(cast(int, item["course_sequence"])))
        continuation = metadata.get("continuation")
        return {
            "schema_version": 1,
            "status": snapshot.session_status.value,
            "shell_status": (
                _readiness_shell_status(snapshot, readiness)
                if isinstance(readiness, StudyReadinessSnapshot)
                else _shell_status(snapshot)
            ),
            "course_id": str(snapshot.course_id),
            "session_id": str(snapshot.session_id),
            "high_water_sequence": snapshot.high_water_sequence,
            "timeline": tuple(canonical_timeline),
            "continuation": _continuation_dto(continuation),
            "capabilities": {
                "propose_flashcards": bool(metadata.get("flashcards_available", False)),
            },
            "mode": "local_repository",
            "title": str(metadata["course_title"]),
        }

    @staticmethod
    def _materials(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        grounding = cast(Mapping[str, object], metadata["source_grounding"])
        groundable = grounding["status"] == "available"
        retired = {
            str(item) for item in cast(tuple[object, ...], metadata.get("retired_source_ids", ()))
        }
        items = cast(
            tuple[JsonObject, ...],
            tuple(
                {
                    "source_id": str(item.source_id),
                    "revision_id": str(item.current_revision_id),
                    "title": item.title,
                    "kind": item.kind.value,
                    "checksum_sha256": item.checksum_sha256,
                    "source_role": item.source_role,
                    "trust_level": item.trust_level,
                    "chunk_count": item.chunk_count,
                    "groundable": groundable,
                    "provenance": {
                        "source_role": item.source_role,
                        "trust_level": item.trust_level,
                    },
                }
                for item in snapshot.materials
                if str(item.source_id) not in retired
            ),
        )
        return {
            "schema_version": 1,
            "status": "ready" if items and groundable else "empty" if not items else "unavailable",
            "high_water_sequence": snapshot.high_water_sequence,
            "items": items,
            "message": (
                "Le fonti del corso sono disponibili per lo studio guidato."
                if items and groundable
                else (
                    "Il testo della fonte non è recuperabile in sicurezza: "
                    "lo studio guidato dalle fonti non è disponibile."
                )
                if items
                else "Questo corso non ha ancora fonti disponibili."
            ),
        }

    @staticmethod
    def _artifacts(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        return _artifact_payload(
            cast(ArtifactSnapshot, metadata["artifacts"]),
            snapshot.session_id,
            recall_snapshot=metadata.get("recall_snapshot"),
            recall_availability=getattr(metadata.get("recall_composition"), "availability", None),
        )

    @staticmethod
    def _assessments(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        return _assessment_payload(
            cast(AssessmentSnapshot, metadata["assessment"]),
            cast(ArtifactSnapshot, metadata["artifacts"]),
            session_id=snapshot.session_id,
        )

    @staticmethod
    def _evidence(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        del snapshot
        return _evidence_payload(metadata.get("evidence"))

    @staticmethod
    def _recall(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        del snapshot
        return cast(JsonObject, metadata["recall_payload"])

    @staticmethod
    def _conflicts(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        del snapshot
        return _context_payload(cast(StudyContextSnapshot, metadata["context"]))

    @staticmethod
    def _plan(snapshot: TutorSnapshotV1, metadata: Mapping[str, object]) -> JsonObject:
        readiness = cast(StudyReadinessSnapshot, metadata["readiness"])
        payload = cast(JsonObject, readiness.to_json())
        status = (
            "conflicted" if getattr(readiness, "deadline_status", "") == "conflicted" else "ready"
        )
        return {
            **payload,
            "status": status,
            "shell_status": _readiness_shell_status(snapshot, readiness),
            "high_water_sequence": readiness.sequence,
            "readiness": payload,
            "message": ("Canonical constraints and observations; no agenda or score is inferred."),
        }


def _artifact_payload(
    snapshot: ArtifactSnapshot,
    session_id: SessionId,
    *,
    recall_snapshot: object | None = None,
    recall_availability: object | None = None,
) -> JsonObject:
    batches = snapshot.batches
    revisions = snapshot.revisions
    selected_batches = tuple(item for item in batches if item.session_id == session_id)
    revision_ids = {revision_id for batch in selected_batches for revision_id in batch.revision_ids}
    selected_revisions = tuple(item for item in revisions if item.id in revision_ids)
    batch_by_revision = {
        revision_id: batch for batch in selected_batches for revision_id in batch.revision_ids
    }
    rows = tuple(
        _artifact_revision_row(
            item,
            batch_by_revision[item.id],
            enrollment_status=_enrollment_status(
                item,
                recall_snapshot,
                recall_availability,
            ),
        )
        for item in sorted(selected_revisions, key=lambda value: (value.proposed_at, str(value.id)))
    )
    sequence = snapshot.sequence
    return {
        "schema_version": 1,
        "status": "ready" if rows else "empty",
        "high_water_sequence": sequence,
        "items": rows,
        "message": (
            "Generated artifacts remain proposals until an explicit decision."
            if rows
            else "No artifact revision belongs to the selected session."
        ),
    }


def _assessment_payload(
    assessment: AssessmentSnapshot,
    artifacts: ArtifactSnapshot,
    *,
    session_id: SessionId | None = None,
) -> JsonObject:
    """Map canonical assessment state to learner-safe presentation DTOs."""

    presentations = assessment.presentations
    attempts = assessment.attempts
    grades = assessment.grades
    contests: tuple[GradeContestRecord, ...] = assessment.contests
    revisions = {item.id: item for item in artifacts.revisions}
    batches = {item.id: item for item in artifacts.batches}
    attempts_by_presentation: dict[PresentationId, AttemptRecord] = {
        item.presentation_id: item for item in attempts
    }
    grades_by_attempt: dict[AttemptId, list[GradeRecord]] = {}
    for grade_record in grades:
        grades_by_attempt.setdefault(grade_record.attempt_id, []).append(grade_record)
    contested = {item.grade_id for item in contests}
    rows: list[JsonObject] = []
    for presentation in sorted(presentations, key=lambda item: (item.presented_at, str(item.id))):
        if session_id is not None and presentation.session_id != session_id:
            continue
        revision = revisions.get(presentation.revision_id)
        if revision is None or revision.status is not ArtifactRevisionStatus.ACCEPTED:
            continue
        if revision.kind is not StudyArtifactKind.ASSESSMENT_ITEM:
            continue
        batch = batches.get(revision.batch_id)
        if batch is None or batch.session_id != presentation.session_id:
            continue
        attempt = attempts_by_presentation.get(presentation.id)
        active_grade = None
        historical_grade = None
        grade_history: list[GradeRecord] = []
        if attempt is not None:
            history = sorted(
                grades_by_attempt.get(attempt.id, []),
                key=lambda item: (item.event_sequence, str(item.id)),
            )
            grade_history = history
            historical_grade = history[-1] if history else None
            active = [item for item in history if item.lifecycle is GradeLifecycle.ACTIVE]
            active_grade = active[-1] if active else None
        if active_grade is not None and active_grade.id in contested:
            status = "contested"
        elif active_grade is not None:
            status = active_grade.status.value
        elif historical_grade is not None:
            status = "superseded"
        elif attempt is not None:
            status = (
                "needs_review"
                if presentation.content.format is AssessmentFormat.FREE_RESPONSE
                else "attempted"
            )
        else:
            status = "ready"
        grade = active_grade or historical_grade
        grade_payload: JsonObject | None = None
        if grade is not None:
            grade_payload = {
                "grade_id": str(grade.id),
                "status": grade.status.value,
                "lifecycle": grade.lifecycle.value,
                "score": {
                    "numerator": grade.score.numerator,
                    "denominator": grade.score.denominator,
                },
            }
        contest_history = tuple(
            item
            for item in contests
            if attempt is not None
            and item.grade_id in {history_grade.id for history_grade in grade_history}
        )
        public_grade_history = tuple(
            {
                "grade_id": str(item.id),
                "status": item.status.value,
                "lifecycle": item.lifecycle.value,
                "supersedes_grade_id": (
                    None if item.supersedes_grade_id is None else str(item.supersedes_grade_id)
                ),
                "active": item.lifecycle is GradeLifecycle.ACTIVE,
                "contested": item.id in contested,
                "recorded_at": item.recorded_at.isoformat(),
                "score": {
                    "numerator": item.score.numerator,
                    "denominator": item.score.denominator,
                },
            }
            for item in grade_history
        )
        public_contests = cast(
            tuple[JsonObject, ...],
            tuple(
                {
                    "grade_id": str(item.grade_id),
                    "disposition": "contested",
                    "contested_at": item.contested_at.isoformat(),
                    "event_sequence": item.event_sequence,
                }
                for item in contest_history
            ),
        )
        can_attempt = attempt is None
        can_grade = (
            attempt is not None
            and active_grade is None
            and historical_grade is None
            and presentation.content.format is not AssessmentFormat.FREE_RESPONSE
        )
        rows.append(
            {
                "presentation_id": str(presentation.id),
                "revision_id": str(presentation.revision_id),
                "format": presentation.content.format.value,
                "prompt": presentation.content.prompt,
                "options": presentation.content.options,
                "attempt_id": None if attempt is None else str(attempt.id),
                "grade_id": None if active_grade is None else str(active_grade.id),
                "active_grade_id": None if active_grade is None else str(active_grade.id),
                "status": status,
                "grading_status": (
                    "needs_review"
                    if status == "needs_review"
                    else "eligible"
                    if can_grade
                    else status
                ),
                "can_attempt": can_attempt,
                "can_grade": can_grade,
                "grade": grade_payload,
                "grade_history": public_grade_history,
                "contests": public_contests,
                "presentation_status": "attempted" if attempt is not None else "presented",
            }
        )
    presented_revision_ids = {row["revision_id"] for row in rows}
    if session_id is not None:
        for revision in sorted(
            revisions.values(), key=lambda item: (item.proposed_at, str(item.id))
        ):
            if (
                revision.status is not ArtifactRevisionStatus.ACCEPTED
                or revision.kind is not StudyArtifactKind.ASSESSMENT_ITEM
            ):
                continue
            batch = batches.get(revision.batch_id)
            if batch is None or batch.session_id != session_id:
                continue
            revision_key = str(revision.id)
            if revision_key in presented_revision_ids:
                continue
            content = revision.content.content
            if not isinstance(content, AssessmentItemContent):
                continue
            rows.append(
                {
                    "presentation_id": None,
                    "revision_id": revision_key,
                    "format": content.format.value,
                    "prompt": content.prompt,
                    "options": content.options,
                    "attempt_id": None,
                    "grade_id": None,
                    "active_grade_id": None,
                    "status": "unpresented",
                    "grading_status": "unavailable",
                    "can_attempt": False,
                    "can_grade": False,
                    "presentation_status": "unpresented",
                    "grade": None,
                    "grade_history": (),
                    "contests": (),
                }
            )
    sequence = getattr(assessment, "sequence", 0)
    return {
        "schema_version": 1,
        "status": "ready" if rows else "empty",
        "high_water_sequence": sequence,
        "items": tuple(rows),
        "presentations": tuple(rows),
        "message": (
            "Accepted assessment presentations for the selected session."
            if rows
            else "No accepted assessment presentation is available for this session."
        ),
    }


def _evidence_payload(snapshot: object) -> JsonObject:
    if snapshot is None:
        return {
            "schema_version": 1,
            "status": "empty",
            "through_sequence": 0,
            "items": (),
            "estimates": (),
            "message": "Non ci sono ancora evidenze registrate dalle verifiche.",
        }
    estimates = tuple(getattr(snapshot, "estimates", ()))
    rows = tuple(
        {
            "dimension": estimate.dimension.value,
            "key": estimate.key,
            "numerator": estimate.numerator,
            "denominator": estimate.denominator,
            "through_sequence": estimate.through_sequence,
            "references": tuple(
                {
                    "grade_id": str(reference.grade_id),
                    "event_sequence": reference.event_sequence,
                    "disposition": reference.disposition.value,
                    "numerator": reference.numerator,
                    "denominator": reference.denominator,
                }
                for reference in estimate.evidence
            ),
        }
        for estimate in estimates
    )
    sequence = getattr(snapshot, "through_sequence", 0)
    return {
        "schema_version": 1,
        "status": "ready" if rows else "empty",
        "through_sequence": sequence,
        "items": rows,
        "estimates": rows,
        "message": (
            "Canonical assessment evidence with attributable ledger references; "
            "no generic mastery percentage is inferred."
            if rows
            else "No canonical assessment evidence has been recorded."
        ),
    }


def _artifact_revision_row(
    revision: ArtifactRevisionRecord,
    batch: ArtifactBatchRecord,
    *,
    enrollment_status: str | None = None,
) -> JsonObject:
    provenance = revision.provenance
    commitments = provenance.source_commitments
    provenance_payload: dict[str, JsonValue] = {
        "origin": provenance.origin.value,
        "source_commitments": tuple(
            {
                "source_id": str(item.source_id),
                "revision_id": str(item.revision_id),
                "chunk_id": str(item.chunk_id),
                "start_offset": item.start_offset,
                "end_offset": item.end_offset,
            }
            for item in commitments
        ),
    }
    if isinstance(provenance, GeneratedArtifactProvenance) and provenance.profile_selection:
        selection = provenance.profile_selection
        provenance_payload["profile_selection"] = {
            "profile": {
                "id": selection.profile.id.value,
                "version": selection.profile.version,
            },
            "mode": selection.mode.value,
            "selector_kind": selection.selector_kind.value,
            "selector_authority": selection.selector_authority.value,
            "basis": {
                "interaction_id": (
                    None
                    if selection.basis.interaction_id is None
                    else str(selection.basis.interaction_id)
                ),
                "source_revision_id": (
                    None
                    if selection.basis.source_revision_id is None
                    else str(selection.basis.source_revision_id)
                ),
            },
        }
    row: dict[str, JsonValue] = {
        "revision_id": str(revision.id),
        "artifact_id": str(revision.artifact_id),
        "batch_id": str(revision.batch_id),
        "session_id": str(batch.session_id),
        "origin": batch.origin.value,
        "ordinal": revision.ordinal,
        "kind": revision.kind.value,
        "status": revision.status.value,
        "proposed_at": revision.proposed_at.isoformat(),
        "decided_at": (None if revision.decided_at is None else revision.decided_at.isoformat()),
        "prior_revision_id": (
            None if revision.prior_revision_id is None else str(revision.prior_revision_id)
        ),
        "provenance": provenance_payload,
    }
    if enrollment_status is not None:
        row["enrollment_status"] = enrollment_status
        row["can_enroll"] = enrollment_status == "not_enrolled"
    return row


def _enrollment_status(
    revision: ArtifactRevisionRecord,
    recall_snapshot: object | None,
    recall_availability: object | None,
) -> str | None:
    if (
        revision.status is not ArtifactRevisionStatus.ACCEPTED
        or revision.kind is not StudyArtifactKind.FLASHCARD
    ):
        return None
    availability = getattr(recall_availability, "code", None)
    if availability is not None and not bool(getattr(recall_availability, "available", False)):
        return str(getattr(availability, "value", availability))
    enrollments = tuple(getattr(recall_snapshot, "enrollments", ()))
    return (
        "enrolled"
        if any(getattr(item, "revision_id", None) == revision.id for item in enrollments)
        else "not_enrolled"
    )


def _recall_payload(
    snapshot: TutorSnapshotV1,
    composition: object,
    artifacts: ArtifactSnapshot,
    *,
    projection: Projection | None = None,
    clock: ClockPort | None = None,
) -> JsonObject:
    availability = getattr(composition, "availability", None)
    availability_json: JsonObject = (
        cast(JsonObject, availability.to_json())
        if availability is not None and callable(getattr(availability, "to_json", None))
        else {
            "available": False,
            "code": "unavailable",
            "message": "Il ripasso programmato non è configurato.",
        }
    )
    rows: list[JsonObject] = []
    due_view = getattr(composition, "due", None)
    joined_artifacts = artifacts
    watermark = snapshot.high_water_sequence
    if projection is not None:
        # A due read must join schedules and accepted artifact content from the
        # same captured high-water projection.  This avoids mixing a fresh
        # schedule with an older artifact snapshot during a concurrent write.
        def projection_loader(_course_id: CourseId) -> Projection:
            return projection

        if clock is None:
            raise ValueError("captured recall projection requires a clock")
        due_view = DueRecallView(projection_loader, clock)
        joined_artifacts = ProjectionArtifactView(projection_loader).get(snapshot.course_id)
        watermark = projection.sequence
    if availability_json.get("code") == "available" and due_view is not None:
        for due in due_view.due(snapshot.course_id):
            try:
                revision = joined_artifacts.revision(due.revision_id)
            except LookupError:
                continue
            content = revision.content.content
            answer_blocks = tuple(getattr(content, "answer_blocks", ()))
            back = "\n".join(
                f"{block.label}: {block.text}"
                for block in answer_blocks
                if isinstance(getattr(block, "label", None), str)
                and isinstance(getattr(block, "text", None), str)
            )
            commitments = tuple(
                {
                    "source_id": str(item.source_id),
                    "revision_id": str(item.revision_id),
                    "chunk_id": str(item.chunk_id),
                    "start_offset": item.start_offset,
                    "end_offset": item.end_offset,
                }
                for item in tuple(getattr(revision.provenance, "source_commitments", ()))[
                    :MAX_RECALL_PROVENANCE_ITEMS
                ]
            )
            rows.append(
                {
                    "artifact_id": due.artifact_id,
                    "revision_id": str(due.revision_id),
                    "status": "due",
                    "due_at": due.due_at.isoformat().replace("+00:00", "Z"),
                    "front": _bounded_recall_text(
                        getattr(content, "prompt", ""), MAX_RECALL_FRONT_CHARS
                    ),
                    "back": _bounded_recall_text(back, MAX_RECALL_BACK_CHARS),
                    "provenance": {"source_commitments": commitments},
                }
            )
    code = str(availability_json.get("code", "unavailable"))
    if code == "available":
        status = "ready" if rows else "empty"
        message = (
            "Accepted flashcards due for review."
            if rows
            else "No accepted enrolled flashcards are due."
        )
    else:
        status = code if code in {"not_configured", "unavailable"} else "unavailable"
        message = str(availability_json.get("message", "Recall is unavailable."))
    return {
        "schema_version": 1,
        "status": status,
        "high_water_sequence": watermark,
        "items": tuple(rows),
        "availability": availability_json,
        "message": message,
    }


def _enrollment_idempotency_key(
    course_id: CourseId, session_id: SessionId, revision_id: ArtifactRevisionId
) -> str:
    raw = f"cardine-recall-enrollment-v1\0{course_id}\0{session_id}\0{revision_id}".encode()
    return f"cardine-recall-enrollment-sha256:{sha256(raw).hexdigest()}"


def _next_schedule_payload(
    snapshot: RecallSnapshot,
    revision_id: ArtifactRevisionId,
    session_id: SessionId,
    request_id: str,
) -> JsonObject:
    review_id = review_id_for(snapshot.course_id, session_id, revision_id, request_id)
    schedules = tuple(
        item
        for item in snapshot.schedules
        if item.trigger == "review"
        and item.revision_id == revision_id
        and item.review_id == review_id
    )
    if len(schedules) != 1:
        raise ValueError("canonical review schedule receipt is missing")
    schedule = schedules[0]
    return {
        "schema_version": 1,
        "high_water_sequence": snapshot.sequence,
        "revision_id": str(schedule.revision_id),
        "review_id": None if schedule.review_id is None else str(schedule.review_id),
        "trigger": schedule.trigger,
        "enrollment_at": schedule.enrollment_at.isoformat().replace("+00:00", "Z"),
        "due_at": schedule.due_at.isoformat().replace("+00:00", "Z"),
        "policy_id": schedule.policy_id,
        "policy_version": schedule.policy_version,
        "implementation_id": schedule.implementation_id,
        "implementation_version": schedule.implementation_version,
    }


def _context_payload(snapshot: StudyContextSnapshot) -> JsonObject:
    conflicts = snapshot.conflicts
    rows: list[JsonObject] = []
    for conflict in conflicts:
        candidates: list[JsonObject] = []
        for statement_id in conflict.statement_ids:
            statement = snapshot.statement(statement_id)
            value = statement.value
            candidates.append(
                {
                    "statement_id": str(statement.id),
                    "value": value.isoformat() if hasattr(value, "isoformat") else value,
                    "provenance": {
                        "session_id": str(statement.session_id),
                        "origin_interaction_id": str(statement.origin_interaction_id),
                        "recorded_at": statement.recorded_at.isoformat(),
                    },
                }
            )
        rows.append(
            {
                "kind": conflict.kind.value,
                "status": "conflicted",
                "candidates": tuple(candidates),
            }
        )
    return {
        "schema_version": 1,
        "status": "ready" if rows else "empty",
        "high_water_sequence": snapshot.sequence,
        "items": tuple(rows),
        "message": (
            "Select a canonical statement; source disagreements remain read-only."
            if rows
            else "No intrinsic learner-context conflict is active."
        ),
    }


def _artifact_decision_target(path: str) -> str | None:
    prefix = "/api/v1/artifacts/"
    suffix = "/decisions"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("artifact revision id is invalid")
    return value


def _recall_enrollment_target(path: str) -> str | None:
    prefix = "/api/v1/recall/"
    suffix = "/enrollments"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("recall enrollment target is invalid")
    return value


def _recall_review_target(path: str) -> str | None:
    prefix = "/api/v1/recall/"
    suffix = "/reviews"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("recall review target is invalid")
    return value


def _assessment_presentation_target(path: str) -> str | None:
    prefix = "/api/v1/assessments/"
    suffix = "/presentations"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("assessment revision id is invalid")
    return value


def _assessment_attempt_target(path: str) -> str | None:
    prefix = "/api/v1/assessments/"
    suffix = "/attempts"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("assessment presentation id is invalid")
    return value


def _assessment_grade_target(path: str) -> str | None:
    prefix = "/api/v1/assessments/"
    suffix = "/grade"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("assessment attempt id is invalid")
    return value


def _assessment_contest_target(path: str) -> str | None:
    prefix = "/api/v1/assessments/grades/"
    suffix = "/contest"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("assessment grade id is invalid")
    return value


def _retry_supersedes_revision(
    revision_id: ArtifactRevisionId,
    decision: ArtifactDecision,
    history: Sequence[ArtifactRevisionRecord],
    expected_fingerprint: str,
) -> ArtifactRevisionId | None:
    """Recover the predecessor named by an already-recorded exact retry."""

    candidates = (
        None,
        *tuple(item.id for item in history),
    )
    for candidate in candidates:
        if (
            decision_command_fingerprint(revision_id, decision, candidate, None)
            == expected_fingerprint
        ):
            return candidate
    raise ArtifactConflictError("artifact retry identity has different command fingerprint")


def _context_resolution_kind(path: str) -> str | None:
    prefix = "/api/v1/context/conflicts/"
    suffix = "/resolve"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if not value or "/" in value or "\\" in value:
        raise UiRequestError("context conflict kind is invalid")
    return value


def _identifier[T: Identifier](value: str | T, identifier_type: type[T], name: str) -> T:
    if isinstance(value, identifier_type):
        return value
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} is invalid")
    return identifier_type(value)


def _workspace_command(
    command: Mapping[str, object],
    *,
    required_keys: set[str],
    optional_keys: set[str] | None = None,
) -> tuple[str, int, Mapping[str, object]]:
    optional_keys = set() if optional_keys is None else optional_keys
    if not isinstance(command, Mapping) or set(command) != {
        "schema_version",
        "request_id",
        "expected_sequence",
        "payload",
    }:
        raise UiRequestError("workspace command shape is invalid")
    if command.get("schema_version") != 1 or isinstance(command.get("schema_version"), bool):
        raise UiRequestError("schema version is unsupported")
    request_id = command.get("request_id")
    expected_sequence = command.get("expected_sequence")
    payload = command.get("payload")
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or len(request_id) > 200
        or not _is_utf8(request_id)
    ):
        raise UiRequestError("request_id is invalid")
    if type(expected_sequence) is not int or expected_sequence < 0:
        raise UiRequestError("expected_sequence is invalid")
    if not isinstance(payload, Mapping):
        raise UiRequestError("workspace command payload is invalid")
    actual = set(payload)
    if not required_keys <= actual or actual - required_keys - optional_keys:
        raise UiRequestError("workspace command payload is invalid")
    return request_id, expected_sequence, payload


def _workspace_text(value: object, name: str, maximum: int) -> str:
    if not isinstance(value, str):
        raise UiRequestError(f"{name} is invalid")
    normalized = value.strip()
    if not normalized or len(normalized) > maximum or not _is_utf8(normalized):
        raise UiRequestError(f"{name} is invalid")
    return normalized


def _lesson_candidate_payload(item: LessonCandidate) -> JsonObject:
    return {
        "candidate_id": item.candidate_id,
        "course_id": item.course_id,
        "source_id": item.source_id,
        "revision_id": item.revision_id,
        "section_title": item.section_title,
        "start_offset": item.start_offset,
        "end_offset": item.end_offset,
        "content_sha256": item.content_sha256,
        "catalog_fingerprint": item.catalog_fingerprint,
    }


def _lesson_pin_payload(pin: SourcePin) -> JsonObject:
    return {
        "course_id": pin.course_id,
        "source_id": pin.source_id,
        "revision_id": pin.revision_id,
        "section_title": pin.section_title,
        "start_offset": pin.start_offset,
        "end_offset": pin.end_offset,
        "content_sha256": pin.content_sha256,
        "catalog_fingerprint": pin.catalog_fingerprint,
    }


def _lesson_pin_payload_from_json(value: object) -> SourcePin:
    if not isinstance(value, Mapping):
        raise UiRequestError("lesson pin is invalid")
    expected = {
        "course_id",
        "source_id",
        "revision_id",
        "section_title",
        "start_offset",
        "end_offset",
        "content_sha256",
        "catalog_fingerprint",
    }
    if set(value) != expected:
        raise UiRequestError("lesson pin is incomplete")
    text_fields = expected - {"start_offset", "end_offset"}
    if any(not isinstance(value.get(key), str) for key in text_fields):
        raise UiRequestError("lesson pin text fields are invalid")
    if any(type(value.get(key)) is not int for key in ("start_offset", "end_offset")):
        raise UiRequestError("lesson pin offsets are invalid")
    try:
        return SourcePin(
            cast(str, value["course_id"]),
            cast(str, value["source_id"]),
            cast(str, value["revision_id"]),
            cast(str, value["section_title"]),
            cast(int, value["start_offset"]),
            cast(int, value["end_offset"]),
            cast(str, value["content_sha256"]),
            cast(str, value["catalog_fingerprint"]),
        )
    except (TypeError, ValueError) as error:
        raise UiRequestError("lesson pin is invalid") from error


def _source_upload_content(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or not _is_utf8(value):
        raise UiRequestError("content is invalid")
    if len(value.encode("utf-8")) > MAX_SOURCE_UPLOAD_BYTES:
        raise UiRequestError("content exceeds the upload limit")
    return value


def _workspace_identifier[T: Identifier](value: object, identifier_type: type[T], name: str) -> T:
    normalized = _workspace_text(value, name, MAX_WORKSPACE_ID_CHARS)
    try:
        return identifier_type(normalized)
    except (TypeError, ValueError) as error:
        raise UiRequestError(f"{name} is invalid") from error


def _workspace_text_list(
    value: object,
    name: str,
    maximum_items: int,
    maximum_chars: int,
    *,
    allow_empty: bool = False,
) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple)):
        raise UiRequestError(f"{name} is invalid")
    if len(value) > maximum_items:
        raise UiRequestError(f"{name} is invalid")
    result = tuple(_workspace_text(item, name, maximum_chars) for item in value)
    if not allow_empty and not result:
        raise UiRequestError(f"{name} is invalid")
    if len(set(result)) != len(result):
        raise UiRequestError(f"{name} is invalid")
    return result


def _workspace_context(
    request_id: str,
    course_id: CourseId,
    session_id: SessionId | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.HUMAN,
        "cardine-ui-owner",
        course_id,
        CorrelationId(f"cardine-workspace-{request_id}"),
        frozenset({"course:write", "session:write"}),
        session_id,
        idempotency_key=request_id,
    )


def _workspace_session(item: object, *, selected: bool) -> JsonObject:
    started_at = getattr(item, "started_at", None)
    return {
        "id": str(getattr(item, "id", "")),
        "status": str(getattr(getattr(item, "status", None), "value", "unknown")),
        "started_at": None if started_at is None else started_at.isoformat(),
        "interaction_count": len(getattr(item, "interaction_ids", ())),
        "answer_count": len(getattr(item, "run_ids", ())),
        "selected": selected,
    }


def _model_check_message(reason: str) -> str:
    return {
        "invalid_credential": "La chiave API non è accettata dal provider.",
        "rate_limited": "Il provider ha limitato le richieste. Riprova tra poco.",
        "timeout": "Il provider non ha risposto entro il tempo previsto.",
        "model_unavailable": "Il modello configurato non è disponibile per questa chiave.",
        "endpoint_incompatible": (
            "L'endpoint del provider non supporta questa configurazione del modello."
        ),
        "provider_unavailable": "Il provider non è raggiungibile in questo momento.",
        "provider_protocol_error": "Il provider ha restituito una risposta non compatibile.",
    }.get(reason, "Il modello non è disponibile.")


def _model_check_reason(failure_reason: object) -> str:
    if not isinstance(failure_reason, str):
        return "provider_unavailable"
    return {
        ModelErrorCode.AUTHENTICATION.value: "invalid_credential",
        ModelErrorCode.RATE_LIMITED.value: "rate_limited",
        ModelErrorCode.TIMEOUT.value: "timeout",
        ModelErrorCode.MODEL_UNAVAILABLE.value: "model_unavailable",
        ModelErrorCode.ENDPOINT_INCOMPATIBLE.value: "endpoint_incompatible",
        ModelErrorCode.UNAVAILABLE.value: "provider_unavailable",
        ModelErrorCode.PROTOCOL_ERROR.value: "provider_protocol_error",
    }.get(failure_reason, "provider_unavailable")


def _timeline_item(item: object) -> JsonObject:
    kind = getattr(getattr(item, "kind", None), "value", "system")
    role = {"learner": "learner", "assistant": "assistant", "note": "system"}.get(
        str(kind), "system"
    )
    occurred_at = getattr(item, "occurred_at", None)
    status = getattr(getattr(item, "status", None), "value", None)
    run_id = getattr(item, "run_id", None)
    reply_id = getattr(item, "in_reply_to_interaction_id", None)
    return {
        "role": role,
        "kind": str(kind),
        "interaction_id": str(getattr(item, "interaction_id", "")),
        "occurred_at": None if occurred_at is None else occurred_at.isoformat(),
        "content": str(getattr(item, "content", "")),
        "event_id": str(getattr(item, "event_id", "")),
        "course_sequence": getattr(item, "course_sequence", 0),
        "run_id": None if run_id is None else str(run_id),
        "status": status,
        "in_reply_to_interaction_id": None if reply_id is None else str(reply_id),
    }


def _presentation_timeline_item(item: object) -> JsonObject:
    kind = str(getattr(getattr(item, "kind", None), "value", "assistant_message"))
    occurred_at = getattr(item, "occurred_at", None)
    reply_id = getattr(item, "in_reply_to_interaction_id", None)
    return {
        "role": "assistant",
        "kind": kind,
        "interaction_id": str(getattr(item, "id", "")),
        "occurred_at": None if occurred_at is None else occurred_at.isoformat(),
        "content": str(getattr(item, "content", "")),
        "event_id": str(getattr(item, "event_id", "")),
        "course_sequence": getattr(item, "course_sequence", 0),
        "run_id": None,
        "status": "pending" if kind == "continuation_request" else "completed",
        "in_reply_to_interaction_id": None if reply_id is None else str(reply_id),
    }


def _active_continuation(
    repository: LocalRepository,
    course_id: CourseId,
    session_id: SessionId,
    presentations: Sequence[object],
) -> PendingContinuationDescriptor | None:
    for item in reversed(tuple(presentations)):
        fingerprint = getattr(item, "continuation_fingerprint", None)
        if not isinstance(fingerprint, str):
            continue
        try:
            record = TutorContinuationRecord.from_bytes(
                repository.tutor_continuations.load(course_id, session_id, fingerprint)
            )
        except (KeyError, OSError, RuntimeError, ValueError):
            continue
        return record.descriptor
    return None


def _continuation_dto(value: object) -> JsonObject | None:
    if not isinstance(value, PendingContinuationDescriptor):
        return None
    return {
        "fingerprint": value.fingerprint,
        "capability_identity": value.capability_identity,
        "dialogue_step_id": value.dialogue_step_id,
        "prompt": value.dialogue_request,
        "response_schema": _thaw_json(value.response_schema),
    }


def _thaw_json(value: JsonValue) -> JsonValue:
    if isinstance(value, Mapping):
        return {str(key): _thaw_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return cast(JsonValue, [_thaw_json(item) for item in value])
    return value


def _continuation_fingerprint(path: str) -> str | None:
    prefix = "/api/v1/session/continuations/"
    suffix = "/responses"
    if not path.startswith(prefix) or not path.endswith(suffix):
        return None
    value = path[len(prefix) : -len(suffix)]
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise UiRequestError("continuation fingerprint is invalid")
    return value


def _shell_status(snapshot: TutorSnapshotV1) -> str:
    if snapshot.session_status.value == "active":
        return "ready"
    if snapshot.session_status.value == "suspended":
        return "suspended"
    return "degraded"


def _repository_mutation_lock(repository: Path) -> Lock:
    key = repository.resolve()
    with _REPOSITORY_LOCKS_GUARD:
        lock = _REPOSITORY_MUTATION_LOCKS.get(key)
        if lock is None:
            lock = Lock()
            _REPOSITORY_MUTATION_LOCKS[key] = lock
        return lock


def _readiness_shell_status(snapshot: TutorSnapshotV1, readiness: StudyReadinessSnapshot) -> str:
    base = _shell_status(snapshot)
    if base != "ready":
        return base
    has_open_work = (
        any(item.status == "conflicted" for item in readiness.constraints)
        or any(item.pending > 0 for item in readiness.artifact_counts)
        or (
            readiness.recall.available
            and readiness.recall.due_count is not None
            and readiness.recall.due_count > 0
        )
    )
    return "needs_review" if has_open_work else base


def _conversation_ui_error(
    error: ConversationTurnError,
    *,
    request_id: str | None = None,
    trace_id: str | None = None,
) -> UiRequestError:
    failure = error.failure_reason
    if failure is not None:
        status, message = {
            "authentication": (503, "configured model credential was rejected"),
            "model_unavailable": (503, "configured model is unavailable"),
            "endpoint_incompatible": (502, "model endpoint is incompatible"),
            "rate_limited": (429, "model request was rate limited"),
            "timeout": (504, "model request timed out"),
            "protocol_error": (502, "model protocol response was invalid"),
            "unavailable": (503, "model provider is unavailable"),
        }[failure]
        return UiRequestError(
            message,
            status_code=status,
            diagnostic_code=f"tutor_{failure}",
            command_committed=error.learner_persisted,
            request_id=request_id if error.learner_persisted else None,
            trace_id=trace_id,
        )
    status = {
        ConversationTurnErrorCode.INVALID_REQUEST: 400,
        ConversationTurnErrorCode.UNAUTHORIZED: 403,
        ConversationTurnErrorCode.NOT_FOUND: 404,
        ConversationTurnErrorCode.CONFLICT: 409,
        ConversationTurnErrorCode.RETRYABLE_CONFLICT: 409,
        ConversationTurnErrorCode.FAILED: 503,
        ConversationTurnErrorCode.INTERRUPTED: 503,
        ConversationTurnErrorCode.INCOMPATIBLE_RUNTIME: 503,
        ConversationTurnErrorCode.CONSENT_REQUIRED: 428,
    }[error.code]
    message = (
        "expected sequence is stale"
        if error.code is ConversationTurnErrorCode.RETRYABLE_CONFLICT
        else "request conflicts with canonical session state"
        if status == 409
        else "tutor execution did not produce a validated response"
        if error.code
        in {
            ConversationTurnErrorCode.FAILED,
            ConversationTurnErrorCode.INTERRUPTED,
        }
        else "repository runtime is unavailable"
        if status == 503
        else "provider consent is required before tutor execution"
        if error.code is ConversationTurnErrorCode.CONSENT_REQUIRED
        else "request is invalid"
        if status == 400
        else "request is not available"
    )
    diagnostic_code = (
        "tutor_execution_failed"
        if error.code
        in {
            ConversationTurnErrorCode.FAILED,
            ConversationTurnErrorCode.INTERRUPTED,
        }
        else "repository_runtime_unavailable"
        if status == 503
        else "provider_consent_required"
        if error.code is ConversationTurnErrorCode.CONSENT_REQUIRED
        else None
    )
    return UiRequestError(
        message,
        status_code=status,
        diagnostic_code=diagnostic_code,
        command_committed=error.learner_persisted,
        request_id=request_id if error.learner_persisted else None,
        trace_id=trace_id,
    )


def _unavailable(result: TutorSnapshotV1 | Mapping[str, object], message: str) -> JsonObject:
    return {
        "schema_version": 1,
        "status": "unavailable",
        "high_water_sequence": (
            result.high_water_sequence if isinstance(result, TutorSnapshotV1) else _sequence(result)
        ),
        "items": (),
        "message": message,
    }


def _command(
    command: Mapping[str, object],
    *,
    payload_key: str | None = "content",
    allow_empty_payload: bool = False,
) -> tuple[str, int, Mapping[str, object]]:
    if not isinstance(command, Mapping) or set(command) != {
        "schema_version",
        "request_id",
        "expected_sequence",
        "payload",
    }:
        raise UiRequestError("command shape is invalid")
    if command.get("schema_version") != 1 or isinstance(command.get("schema_version"), bool):
        raise UiRequestError("schema version is unsupported")
    request_id = command.get("request_id")
    expected = command.get("expected_sequence")
    payload = command.get("payload")
    if (
        not isinstance(request_id, str)
        or not request_id
        or request_id != request_id.strip()
        or len(request_id) > 200
        or not _is_utf8(request_id)
    ):
        raise UiRequestError("request_id is invalid")
    if type(expected) is not int or expected < 0:
        raise UiRequestError("expected_sequence is invalid")
    expected_payload_keys = set() if payload_key is None else {payload_key}
    actual_payload_keys = set(payload) if isinstance(payload, Mapping) else None
    if allow_empty_payload:
        valid_payload = actual_payload_keys in (set(), {"supersedes_grade_id"})
    else:
        valid_payload = actual_payload_keys == expected_payload_keys
    if not isinstance(payload, Mapping) or not valid_payload:
        raise UiRequestError("command payload is invalid")
    return request_id, expected, payload


def _optional_grade_id(payload: Mapping[str, object]) -> GradeId | None:
    if not payload:
        return None
    if set(payload) != {"supersedes_grade_id"}:
        raise UiRequestError("grade command payload is invalid")
    value = payload.get("supersedes_grade_id")
    if not isinstance(value, str) or not value.strip():
        raise UiRequestError("supersedes_grade_id is invalid")
    try:
        return GradeId(value)
    except ValueError as error:
        raise UiRequestError("supersedes_grade_id is invalid") from error


def _canonical_browser_response(
    value: object,
    expected_format: AssessmentFormat,
    options: tuple[str, ...],
) -> FreeResponse | SingleChoiceResponse | MultipleChoiceResponse:
    if not isinstance(value, Mapping) or not isinstance(value.get("kind"), str):
        raise UiRequestError("assessment response union is invalid")
    kind = value.get("kind")
    if expected_format is AssessmentFormat.FREE_RESPONSE:
        if kind != "free_response" or set(value) != {"kind", "text"}:
            raise UiRequestError("free-response kind is invalid")
        return FreeResponse(_bounded_content(value.get("text")))
    if expected_format is AssessmentFormat.SINGLE_CHOICE:
        raw = value.get("selected_option")
        if (
            kind != "single_choice"
            or set(value) != {"kind", "selected_option"}
            or not isinstance(raw, str)
            or raw not in options
        ):
            raise UiRequestError("single-choice response is invalid")
        return SingleChoiceResponse(raw)
    raw = value.get("selected_options")
    if (
        kind != "multiple_choice"
        or set(value) != {"kind", "selected_options"}
        or not isinstance(raw, list)
    ):
        raise UiRequestError("multiple-choice response is invalid")
    if not raw or any(not isinstance(item, str) or item not in options for item in raw):
        raise UiRequestError("multiple-choice response is invalid")
    if len(raw) != len(set(raw)):
        raise UiRequestError("multiple-choice response is invalid")
    return MultipleChoiceResponse(tuple(raw))


def _bounded_content(value: object) -> str:
    if not isinstance(value, str):
        raise UiRequestError("content is invalid")
    content = value.strip()
    if not content or len(content) > MAX_LEARNER_ENTRY_CHARS or not _is_utf8(content):
        raise UiRequestError("content is invalid")
    return content


def _bounded_recall_text(value: object, maximum: int) -> str:
    """Return bounded learner-safe card text with an explicit truncation mark."""

    if not isinstance(value, str):
        return ""
    text = value.strip()
    if len(text) <= maximum:
        return text
    return text[: max(1, maximum - 1)].rstrip() + "…"


def _is_utf8(value: str) -> bool:
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _sequence(result: Mapping[str, object]) -> int:
    value = result.get("evidence_sequence", result.get("evidence_refresh_sequence"))
    return value if type(value) is int and value >= 0 else 0


def _mapping(value: object) -> Mapping[str, object]:
    return value if isinstance(value, Mapping) else {}


def _objects(value: object) -> tuple[JsonObject, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(cast(JsonObject, dict(item)) for item in value if isinstance(item, Mapping))


def _strings(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)):
        return ()
    return tuple(item for item in value if isinstance(item, str))


def _integer(value: object, *, default: int) -> int:
    return value if type(value) is int else default


def _surface_value(result: object) -> JsonObject:
    """Translate the shared typed surface failure once at the UI boundary."""

    error = getattr(result, "error", None)
    if error is not None:
        code = getattr(error, "code", None)
        status = 409 if getattr(code, "value", "") in {"conflict", "retryable_conflict"} else 400
        raise UiRequestError(
            str(getattr(error, "message", "study operation failed")), status_code=status
        )
    value = getattr(result, "value", None)
    if not isinstance(value, Mapping):
        raise UiRequestError("study operation returned an invalid result", status_code=503)
    return cast(JsonObject, dict(value))


def _surface_success(result: object) -> None:
    _surface_value(result)


def _surface_profile(result: object) -> CourseProfile:
    value = _surface_value(result)
    raw = value.get("profile")
    if not isinstance(raw, Mapping):
        raise UiRequestError("course creation returned an invalid profile", status_code=503)
    return CourseProfile(
        CourseId(str(raw["id"])),
        str(raw["title"]),
        str(raw["language"]),
        learning_goals=_strings(raw.get("learning_goals")),
        assessment_styles=_strings(raw.get("assessment_styles")),
    )


__all__ = [
    "RepositoryUiApplication",
    "UiApplicationPort",
    "UiRequestError",
]
