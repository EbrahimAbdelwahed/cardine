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

from cardine.application.conversation_history import (
    ConversationHistoryEntry,
    ConversationHistoryReader,
)
from cardine.application.study_memory import StudyMemoryArchive, StudyMemoryEntry
from cardine.courses import course_profile_manifest
from cardine.courses.service import CourseService
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
from study_agent.sessions.service import IdempotencyConflictError, SessionService
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
    conversation_history: object
    study_memory: object
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

    def __init__(
        self,
        owner: HarnessToolOwner,
        *,
        conversation_through_sequence: int | None = None,
    ) -> None:
        if conversation_through_sequence is not None and (
            type(conversation_through_sequence) is not int
            or conversation_through_sequence < 0
        ):
            raise ValueError("conversation high-water bound is invalid")
        self._owner = owner
        self._conversation_through_sequence = conversation_through_sequence
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
        except IdempotencyConflictError:
            return _failure(ToolErrorCode.CONFLICT, "tool retry conflicts with canonical state")
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
                    "study_memory.record",
                    _object(
                        {
                            "topic": {"type": "string", "minLength": 1, "maxLength": 120},
                            "summary": {
                                "type": "string",
                                "minLength": 1,
                                "maxLength": 300,
                            },
                            "signal": {
                                "type": "string",
                                "enum": (
                                    "self_reported_difficulty",
                                    "incorrect",
                                    "partial",
                                    "correct",
                                    "unknown",
                                ),
                            },
                            "assistance": {
                                "type": "string",
                                "enum": ("none", "hint", "explanation", "unknown"),
                            },
                        },
                        ("topic", "summary", "signal", "assistance"),
                    ),
                    output_schema=_object(
                        {
                            "memory_id": _TEXT,
                            "kind": {"type": "string", "enum": ("learner_signal",)},
                            "origin_sequence": {"type": "integer", "minimum": 1},
                            "recorded_sequence": {"type": "integer", "minimum": 1},
                        },
                        ("memory_id", "kind", "origin_sequence", "recorded_sequence"),
                    ),
                    effect=ToolEffect.CANONICAL_WRITE,
                    capability="study:write",
                    idempotency=IdempotencyMode.REQUIRED,
                ),
                self._record_study_memory,
            ),
            _Operation(
                _manifest(
                    "study_memory.search",
                    _object(
                        {
                            "query": {
                                "type": ("string", "null"),
                            },
                            "kind": {
                                "type": "string",
                                "enum": ("any", "topic_covered", "learner_signal"),
                            },
                            "signal": {
                                "type": "string",
                                "enum": (
                                    "any",
                                    "self_reported_difficulty",
                                    "incorrect",
                                    "partial",
                                    "correct",
                                    "unknown",
                                ),
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                        },
                        ("query", "kind", "signal", "limit"),
                    ),
                    output_schema=_object(
                        {"entries": _array(_study_memory_entry_schema())},
                        ("entries",),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._search_study_memory,
            ),
            _Operation(
                _manifest(
                    "conversation.search",
                    _object(
                        {
                            "query": {"type": "string", "minLength": 1, "maxLength": 240},
                            "limit": {"type": "integer", "minimum": 1, "maximum": 8},
                        },
                        ("query", "limit"),
                    ),
                    output_schema=_object(
                        {
                            "entries": _array(_history_entry_schema()),
                            "total_entries": {"type": "integer", "minimum": 0},
                            "match_count": {"type": "integer", "minimum": 0},
                            "through_sequence": {"type": "integer", "minimum": 0},
                        },
                        ("entries", "total_entries", "match_count", "through_sequence"),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._search_conversation,
            ),
            _Operation(
                _manifest(
                    "conversation.read",
                    _object(
                        {
                            "cursor": {"type": ("integer", "null")},
                            "direction": {
                                "type": "string",
                                "enum": ("backward", "forward"),
                            },
                            "limit": {"type": "integer", "minimum": 1, "maximum": 12},
                        },
                        ("cursor", "direction", "limit"),
                    ),
                    output_schema=_object(
                        {
                            "entries": _array(_history_entry_schema()),
                            "total_entries": {"type": "integer", "minimum": 0},
                            "through_sequence": {"type": "integer", "minimum": 0},
                            "next_cursor": {
                                "type": ("integer", "null"),
                            },
                            "has_more": _BOOL,
                        },
                        (
                            "entries",
                            "total_entries",
                            "through_sequence",
                            "next_cursor",
                            "has_more",
                        ),
                    ),
                    effect=ToolEffect.READ_ONLY,
                    capability="study:read",
                ),
                self._read_conversation,
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

    def _search_conversation(
        self, arguments: JsonObject, context: ExecutionContext
    ) -> ToolResult:
        if context.session_id is None:
            return _failure(ToolErrorCode.UNAUTHORIZED, "conversation scope is host-derived")
        result = cast(ConversationHistoryReader, self._owner.conversation_history).search(
            context.course_id,
            context.session_id,
            str(arguments["query"]),
            limit=cast(int, arguments["limit"]),
            through_sequence=self._conversation_through_sequence,
        )
        return ToolResult.success(
            {
                "entries": tuple(_history_entry(item, 700) for item in result.entries),
                "total_entries": result.total_entries,
                "match_count": result.match_count,
                "through_sequence": result.through_sequence,
            }
        )

    def _record_study_memory(
        self, arguments: JsonObject, context: ExecutionContext
    ) -> ToolResult:
        if context.session_id is None:
            return _failure(ToolErrorCode.UNAUTHORIZED, "study memory scope is host-derived")
        archive = cast(StudyMemoryArchive, self._owner.study_memory)
        origin_sequence = archive.latest_learner_sequence(
            context.course_id,
            context.session_id,
            through_sequence=self._conversation_through_sequence,
        )
        entry = archive.record_learner_signal(
            topic=str(arguments["topic"]),
            summary=str(arguments["summary"]),
            signal=str(arguments["signal"]),
            assistance=str(arguments["assistance"]),
            context=context,
            origin_sequence=origin_sequence,
        )
        return ToolResult.success(
            {
                "memory_id": entry.memory_id,
                "kind": entry.kind,
                "origin_sequence": entry.origin_sequence,
                "recorded_sequence": entry.recorded_sequence,
            }
        )

    def _search_study_memory(
        self, arguments: JsonObject, context: ExecutionContext
    ) -> ToolResult:
        entries = cast(StudyMemoryArchive, self._owner.study_memory).search(
            context.course_id,
            query=cast(str | None, arguments["query"]),
            kind=str(arguments["kind"]),
            signal=str(arguments["signal"]),
            limit=cast(int, arguments["limit"]),
            through_sequence=self._conversation_through_sequence,
        )
        return ToolResult.success(
            {"entries": tuple(_study_memory_entry(item) for item in entries)}
        )

    def _read_conversation(
        self, arguments: JsonObject, context: ExecutionContext
    ) -> ToolResult:
        if context.session_id is None:
            return _failure(ToolErrorCode.UNAUTHORIZED, "conversation scope is host-derived")
        result = cast(ConversationHistoryReader, self._owner.conversation_history).read(
            context.course_id,
            context.session_id,
            cursor=cast(int | None, arguments.get("cursor")),
            direction=str(arguments["direction"]),
            limit=cast(int, arguments["limit"]),
            through_sequence=self._conversation_through_sequence,
        )
        return ToolResult.success(
            {
                "entries": tuple(_history_entry(item, 500) for item in result.entries),
                "total_entries": result.total_entries,
                "through_sequence": result.through_sequence,
                "next_cursor": result.next_cursor,
                "has_more": result.has_more,
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


def _history_entry_schema() -> JsonObject:
    return _object(
        {
            "role": {"type": "string", "enum": ("learner", "assistant")},
            "course_sequence": {"type": "integer", "minimum": 1},
            "excerpt": _TEXT,
        },
        ("role", "course_sequence", "excerpt"),
    )


def _history_entry(entry: ConversationHistoryEntry, maximum: int) -> JsonObject:
    return {
        "role": entry.role,
        "course_sequence": entry.course_sequence,
        "excerpt": entry.excerpt[:maximum],
    }


def _study_memory_entry_schema() -> JsonObject:
    nullable_text: JsonObject = {"type": ("string", "null")}
    return _object(
        {
            "memory_id": _TEXT,
            "kind": {"type": "string", "enum": ("topic_covered", "learner_signal")},
            "topic": _TEXT,
            "summary": nullable_text,
            "signal": nullable_text,
            "assistance": nullable_text,
            "origin_sequence": {"type": "integer", "minimum": 1},
            "recorded_sequence": {"type": "integer", "minimum": 1},
            "recorded_by": {"type": "string", "enum": ("host", "tutor_agent")},
        },
        (
            "memory_id",
            "kind",
            "topic",
            "summary",
            "signal",
            "assistance",
            "origin_sequence",
            "recorded_sequence",
            "recorded_by",
        ),
    )


def _study_memory_entry(entry: StudyMemoryEntry) -> JsonObject:
    return {
        "memory_id": entry.memory_id,
        "kind": entry.kind,
        "topic": entry.topic,
        "summary": entry.summary,
        "signal": entry.signal,
        "assistance": entry.assistance,
        "origin_sequence": entry.origin_sequence,
        "recorded_sequence": entry.recorded_sequence,
        "recorded_by": entry.recorded_by,
    }


__all__ = ["HarnessToolSurface"]
