"""Private, canonical operations shared by Cardine's UI and tutor host.

This is deliberately separate from ``study_agent.tools.StudyToolRegistry``.
The latter is a released seven-tool public contract; Cardine needs a broader
product composition without silently changing those published manifests.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from hashlib import sha256
from typing import Protocol, cast

from study_agent.courses import course_profile_manifest
from study_agent.courses.service import CourseService
from study_agent.domain import (
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ingestion import TextIngestionError
from study_agent.ingestion.service import TextIngestionService
from study_agent.ports import (
    ArtifactViewPort,
    AssessmentViewPort,
    CourseCatalogPort,
    EventStore,
    LearnerEvidenceViewPort,
    StudyContextViewPort,
)
from study_agent.recall.composition import RecallComposition
from study_agent.sessions.service import SessionService
from study_agent.state import Projection
from study_agent.tools import (
    IdempotencyMode,
    ToolEffect,
    ToolError,
    ToolErrorCode,
    ToolManifest,
    ToolResult,
)
from study_agent.tools.schema import (
    SchemaValidationError,
    validate_json,
    validate_schema_definition,
)


class HarnessToolOwner(Protocol):
    """Narrow repository view required by the product operation surface."""

    courses: object
    course_catalog: object
    course_service: object
    sessions: object
    session_service: object
    study_context: object
    artifacts: object
    assessments: object
    learner_evidence: object
    recall_composition: object
    events: object

    def for_course(self, course_id: CourseId) -> object: ...

    def study_tools(self, course_id: CourseId) -> object: ...


class _CourseServices(Protocol):
    ingestion: TextIngestionService


class _EventProjectionStore(EventStore, Protocol):
    def projection(self, course_id: CourseId) -> Projection: ...


_ERRORS = tuple(ToolErrorCode)
_TEXT: JsonObject = {"type": "string", "minLength": 1}
_BOOL: JsonObject = {"type": "boolean"}


def _object(properties: JsonObject, required: tuple[str, ...] = ()) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _array(items: JsonObject) -> JsonObject:
    return {"type": "array", "items": items}


def _profile_schema() -> JsonObject:
    return _object(
        {
            "id": _TEXT,
            "title": _TEXT,
            "language": _TEXT,
            "exam_date": {"type": ("string", "null")},
            "assessment_styles": _array(_TEXT),
            "learning_goals": _array(_TEXT),
            "source_policy": _object(
                {
                    "allowed_roles": _array(_TEXT),
                    "minimum_trust_level": {"type": "integer", "minimum": 0, "maximum": 100},
                },
                ("allowed_roles", "minimum_trust_level"),
            ),
            "terminology_policy": _object(
                {
                    "entries": _array(
                        _object(
                            {"concept": _TEXT, "preferred_term": _TEXT},
                            ("concept", "preferred_term"),
                        )
                    )
                },
                ("entries",),
            ),
        },
        (
            "id",
            "title",
            "language",
            "exam_date",
            "assessment_styles",
            "learning_goals",
            "source_policy",
            "terminology_policy",
        ),
    )


def _manifest(
    name: str,
    input_schema: JsonObject,
    *,
    output_schema: JsonObject,
    effect: ToolEffect,
    capability: str,
    idempotency: IdempotencyMode = IdempotencyMode.NOT_APPLICABLE,
) -> ToolManifest:
    return ToolManifest(
        name=name,
        version="1.0.0",
        input_schema=input_schema,
        output_schema=output_schema,
        effect=effect,
        required_capabilities=(capability,),
        emitted_event_kinds=(),
        error_codes=_ERRORS,
        idempotency=idempotency,
    )


@dataclass(frozen=True, slots=True)
class _Operation:
    manifest: ToolManifest
    invoke: Callable[[JsonObject, ExecutionContext], ToolResult]


class HarnessToolSurface:
    """Closed repository-backed product operations.

    The surface performs argument/grant checks once and delegates every write
    to its existing canonical application service.  It never owns state and
    it intentionally has no HTTP, browser, or provider dependency.
    """

    def __init__(self, owner: HarnessToolOwner) -> None:
        self._owner = owner
        self._operations = {item.manifest.name: item for item in self._build_operations()}
        if len(self._operations) != len(self._build_operations()):
            raise RuntimeError("harness tool names must be unique")
        for operation in self._operations.values():
            validate_schema_definition(operation.manifest.input_schema)
            validate_schema_definition(operation.manifest.output_schema)

    @property
    def manifests(self) -> tuple[ToolManifest, ...]:
        """The complete product operation vocabulary, canonically ordered."""

        return tuple(self._operations[name].manifest for name in sorted(self._operations))

    async def invoke(
        self, name: str, arguments: JsonObject, context: ExecutionContext
    ) -> ToolResult:
        operation = self._operations.get(name)
        if operation is None:
            return _failure(ToolErrorCode.INVALID_ARGUMENTS, "unknown harness tool")
        if not isinstance(context.principal_kind, PrincipalKind):
            return _failure(ToolErrorCode.UNAUTHORIZED, "execution principal is not trusted")
        if not set(operation.manifest.required_capabilities) <= context.requested_capabilities:
            return _failure(ToolErrorCode.UNAUTHORIZED, "required capability was not granted")
        if (
            operation.manifest.idempotency is IdempotencyMode.REQUIRED
            and context.idempotency_key is None
        ):
            return _failure(ToolErrorCode.INVALID_ARGUMENTS, "tool invocation requires idempotency")
        try:
            validate_json(arguments, operation.manifest.input_schema)
            result = operation.invoke(arguments, context)
            if result.value is not None:
                validate_json(result.value, operation.manifest.output_schema)
            return result
        except (SchemaValidationError, TypeError, ValueError):
            return _failure(
                ToolErrorCode.INVALID_ARGUMENTS, "tool arguments violate the study contract"
            )
        except LookupError:
            return _failure(ToolErrorCode.NOT_FOUND, "requested study state was not found")
        except TextIngestionError as error:
            return _failure(
                ToolErrorCode.CONFLICT if error.retryable else ToolErrorCode.INVALID_ARGUMENTS,
                str(error),
            )
        except Exception:
            return _failure(ToolErrorCode.EXECUTION_FAILED, "canonical operation failed safely")

    def _build_operations(self) -> tuple[_Operation, ...]:
        return (
            _Operation(
                _manifest(
                    "course.create",
                    _object(
                        {
                            "course_id": _TEXT,
                            "title": _TEXT,
                            "language": _TEXT,
                            "learning_goals": _array(_TEXT),
                            "assessment_styles": _array(_TEXT),
                        },
                        ("title", "language", "learning_goals"),
                    ),
                    output_schema=_object({"profile": _profile_schema()}, ("profile",)),
                    effect=ToolEffect.CANONICAL_WRITE,
                    capability="course:write",
                    idempotency=IdempotencyMode.REQUIRED,
                ),
                self._create_course,
            ),
            _Operation(
                _manifest(
                    "course.list",
                    _object({}),
                    output_schema=_object({"courses": _array(_profile_schema())}, ("courses",)),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._list_courses,
            ),
            _Operation(
                _manifest(
                    "session.start",
                    _object({"session_id": _TEXT}, ("session_id",)),
                    output_schema=_object(
                        {
                            "id": _TEXT,
                            "status": _TEXT,
                            "high_water_sequence": {"type": "integer", "minimum": 0},
                        },
                        ("id", "status", "high_water_sequence"),
                    ),
                    effect=ToolEffect.CANONICAL_WRITE,
                    capability="session:write",
                    idempotency=IdempotencyMode.REQUIRED,
                ),
                self._start_session,
            ),
            _Operation(
                _manifest(
                    "source.ingest",
                    _object(
                        {"filename": _TEXT, "title": _TEXT, "content": _TEXT},
                        ("filename", "title", "content"),
                    ),
                    output_schema=_object(
                        {
                            "status": _TEXT,
                            "source_id": _TEXT,
                            "revision_id": _TEXT,
                            "title": _TEXT,
                            "chunk_count": {"type": "integer", "minimum": 0},
                            "high_water_sequence": {"type": "integer", "minimum": 0},
                        },
                        (
                            "status",
                            "source_id",
                            "revision_id",
                            "title",
                            "chunk_count",
                            "high_water_sequence",
                        ),
                    ),
                    effect=ToolEffect.CANONICAL_WRITE,
                    capability="source:write",
                    idempotency=IdempotencyMode.REQUIRED,
                ),
                self._ingest_source,
            ),
            _Operation(
                _manifest(
                    "context.get",
                    _object({}),
                    output_schema=_object(
                        {
                            "sequence": {"type": "integer", "minimum": 0},
                            "statement_count": {"type": "integer", "minimum": 0},
                            "conflict_count": {"type": "integer", "minimum": 0},
                        },
                        ("sequence", "statement_count", "conflict_count"),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._context,
            ),
            _Operation(
                _manifest(
                    "recall.get",
                    _object({}),
                    output_schema=_object(
                        {
                            "available": _BOOL,
                            "sequence": {"type": "integer", "minimum": 0},
                            "enrollment_count": {"type": "integer", "minimum": 0},
                            "review_count": {"type": "integer", "minimum": 0},
                        },
                        ("available", "sequence", "enrollment_count", "review_count"),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._recall,
            ),
            _Operation(
                _manifest(
                    "artifact.get",
                    _object({}),
                    output_schema=_object(
                        {
                            "sequence": {"type": "integer", "minimum": 0},
                            "pending_revision_ids": _array(_TEXT),
                            "accepted_revision_ids": _array(_TEXT),
                        },
                        ("sequence", "pending_revision_ids", "accepted_revision_ids"),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._artifacts,
            ),
            _Operation(
                _manifest(
                    "assessment.get",
                    _object({}),
                    output_schema=_object(
                        {
                            "sequence": {"type": "integer", "minimum": 0},
                            "presentations": _array(_TEXT),
                            "attempts": _array(_TEXT),
                            "grades": _array(_TEXT),
                        },
                        ("sequence", "presentations", "attempts", "grades"),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._assessments,
            ),
            _Operation(
                _manifest(
                    "evidence.get",
                    _object({}),
                    output_schema=_object(
                        {
                            "through_sequence": {"type": "integer", "minimum": 0},
                            "estimates": _array(
                                _object(
                                    {
                                        "dimension": _TEXT,
                                        "key": _TEXT,
                                        "label": _TEXT,
                                        "numerator": {"type": "integer", "minimum": 0},
                                        "denominator": {"type": "integer", "minimum": 0},
                                    },
                                    ("dimension", "key", "label", "numerator", "denominator"),
                                )
                            ),
                        },
                        ("through_sequence", "estimates"),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._evidence,
            ),
        )

    def _create_course(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        course_id = CourseId(str(arguments.get("course_id", context.course_id)))
        if context.course_id != course_id or context.session_id is not None:
            return _failure(ToolErrorCode.UNAUTHORIZED, "course authority is host-derived")
        profile = CourseProfile(
            course_id,
            str(arguments["title"]),
            str(arguments["language"]),
            learning_goals=cast(tuple[str, ...], arguments["learning_goals"]),
            assessment_styles=cast(
                tuple[str, ...], arguments.get("assessment_styles", ())
            ),
        )
        created = cast(CourseService, self._owner.course_service).create(profile, context)
        return ToolResult.success({"profile": course_profile_manifest(created)})

    def _list_courses(self, _arguments: JsonObject, _context: ExecutionContext) -> ToolResult:
        courses = tuple(
            course_profile_manifest(item)
            for item in cast(CourseCatalogPort, self._owner.course_catalog).list_courses()
        )
        return ToolResult.success({"courses": courses})

    def _start_session(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        session_id = SessionId(str(arguments["session_id"]))
        if context.session_id != session_id:
            return _failure(ToolErrorCode.UNAUTHORIZED, "session authority is host-derived")
        session = cast(SessionService, self._owner.session_service).start(context)
        sequence = cast(_EventProjectionStore, self._owner.events).projection(
            context.course_id
        ).sequence
        return ToolResult.success(
            {"id": str(session.id), "status": session.status.value, "high_water_sequence": sequence}
        )

    def _ingest_source(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        filename, title, content = (str(arguments[key]) for key in ("filename", "title", "content"))
        encoded = content.encode("utf-8")
        source_id = SourceId(
            "source-upload-sha256:" + sha256(filename.encode("utf-8") + b"\0" + encoded).hexdigest()
        )
        course = cast(_CourseServices, self._owner.for_course(context.course_id))
        result = course.ingestion.ingest(
            filename=filename,
            content=encoded,
            source_id=source_id,
            title=title,
            trust_level=80,
            source_role="learner_uploaded",
            context=context,
            expected_sequence=None,
        )
        return ToolResult.success(
            {
                "status": result.status.value,
                "source_id": str(result.source.source_id),
                "revision_id": str(result.source.revision_id),
                "title": result.source.title,
                "chunk_count": len(result.chunks),
                "high_water_sequence": result.committed_sequence,
            }
        )

    def _context(self, _arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        snapshot = cast(StudyContextViewPort, self._owner.study_context).get(
            context.course_id
        )
        return ToolResult.success(
            {
                "sequence": snapshot.sequence,
                "statement_count": len(snapshot.statements),
                "conflict_count": len(snapshot.conflicts),
            }
        )

    def _recall(self, _arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        composition = cast(RecallComposition, self._owner.recall_composition)
        snapshot = composition.view.get(context.course_id)
        return ToolResult.success(
            {
                "available": composition.availability.available,
                "sequence": snapshot.sequence,
                "enrollment_count": len(snapshot.enrollments),
                "review_count": len(snapshot.reviews),
            }
        )

    def _artifacts(self, _arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        snapshot = cast(ArtifactViewPort, self._owner.artifacts).get(context.course_id)
        return ToolResult.success(
            {
                "sequence": snapshot.sequence,
                "pending_revision_ids": tuple(str(item.id) for item in snapshot.pending()),
                "accepted_revision_ids": tuple(str(item.id) for item in snapshot.accepted()),
            }
        )

    def _assessments(self, _arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        snapshot = cast(AssessmentViewPort, self._owner.assessments).get(context.course_id)
        return ToolResult.success(
            {
                "sequence": snapshot.sequence,
                "presentations": tuple(str(item.id) for item in snapshot.presentations),
                "attempts": tuple(str(item.id) for item in snapshot.attempts),
                "grades": tuple(str(item.id) for item in snapshot.grades),
            }
        )

    def _evidence(self, _arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        snapshot = cast(LearnerEvidenceViewPort, self._owner.learner_evidence).get(
            context.course_id
        )
        return ToolResult.success(
            {
                "through_sequence": snapshot.through_sequence,
                "estimates": tuple(
                    {
                        "dimension": item.dimension.value,
                        "key": item.key,
                        "label": item.label,
                        "numerator": item.numerator,
                        "denominator": item.denominator,
                    }
                    for item in snapshot.estimates
                ),
            }
        )


def _failure(code: ToolErrorCode, message: str) -> ToolResult:
    return ToolResult.failure(
        ToolError(code, message, retryable=code is ToolErrorCode.RETRYABLE_CONFLICT)
    )


__all__ = ["HarnessToolSurface"]
