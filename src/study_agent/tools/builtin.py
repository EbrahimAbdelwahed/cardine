"""The exact v0.1 public study tools, as thin application-service adapters."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, cast

from cardine.courses import course_profile_manifest
from study_agent.domain import ChunkId, Citation, ExecutionContext, RevisionId, SourceId, SourceKind
from study_agent.domain._validation import JsonObject, freeze_object
from study_agent.ports import CourseViewPort, RetrievalPort, RetrievalQuery, SourceContentPort
from study_agent.ports.retrieval import RetrievalCatalogPort
from study_agent.sessions import SessionService, summary_payload
from study_agent.sessions.events import grounded_answer_manifest
from study_agent.state import canonical_json_bytes

from .contracts import (
    IdempotencyMode,
    StudyEvent,
    StudyEventKind,
    ToolEffect,
    ToolErrorCode,
    ToolManifest,
    ToolResult,
)

if TYPE_CHECKING:
    from cardine.application.grounding_ask import GroundingAskService, GroundingStudyEvent

_VERSION = "1.0.0"
_ERRORS = tuple(ToolErrorCode)


@dataclass(slots=True)
class GroundingAskServiceProvider:
    """Resolve and retain the model-backed service when ``grounding.ask`` first runs."""

    factory: Callable[[], GroundingAskService]
    _service: GroundingAskService | None = field(default=None, init=False, repr=False)

    def resolve(self) -> GroundingAskService:
        service = self._service
        if service is None:
            service = self.factory()
            self._service = service
        return service


def _object(properties: JsonObject, required: tuple[str, ...] = ()) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


def _array(items: JsonObject, *, maximum: int | None = None) -> JsonObject:
    schema: dict[str, object] = {"type": "array", "items": items}
    if maximum is not None:
        schema["maxItems"] = maximum
    return schema  # type: ignore[return-value]


_TEXT: JsonObject = {"type": "string", "minLength": 1}
_STRING: JsonObject = {"type": "string"}
_NULLABLE_STRING: JsonObject = {"type": ("string", "null")}
_BOOL: JsonObject = {"type": "boolean"}
_INT: JsonObject = {"type": "integer"}
_SCORE: JsonObject = {"type": "number", "minimum": 0, "maximum": 1}
_TRUST: JsonObject = {"type": "integer", "minimum": 0, "maximum": 100}
_CITATION_INPUT = _object(
    {
        "source_id": _TEXT,
        "revision_id": _TEXT,
        "chunk_id": _TEXT,
        "start_offset": {"type": "integer", "minimum": 0},
        "end_offset": {"type": "integer", "minimum": 1},
    },
    ("source_id", "revision_id", "chunk_id", "start_offset", "end_offset"),
)
_CITATION_OUTPUT = _object(
    {
        **dict(_CITATION_INPUT["properties"]),  # type: ignore[arg-type]
        "locator": _TEXT,
        "quoted_snippet": _TEXT,
    },
    (
        "source_id",
        "revision_id",
        "chunk_id",
        "start_offset",
        "end_offset",
        "locator",
        "quoted_snippet",
    ),
)
_PROFILE_SCHEMA = _object(
    {
        "id": _TEXT,
        "title": _TEXT,
        "language": _TEXT,
        "exam_date": _NULLABLE_STRING,
        "assessment_styles": _array(_TEXT),
        "learning_goals": _array(_TEXT),
        "source_policy": _object(
            {"allowed_roles": _array(_TEXT), "minimum_trust_level": _TRUST},
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
    output_schema: JsonObject,
    *,
    effect: ToolEffect = ToolEffect.READ_ONLY,
    capability: str = "study:read",
    idempotency: IdempotencyMode = IdempotencyMode.NOT_APPLICABLE,
    events: tuple[str, ...] = (),
) -> ToolManifest:
    return ToolManifest(
        name,
        _VERSION,
        input_schema,
        output_schema,
        effect,
        (capability,),
        events,
        _ERRORS,
        idempotency,
    )


@dataclass(frozen=True, slots=True)
class CourseGetTool:
    courses: CourseViewPort
    manifest = _manifest(
        "course.get",
        _object({}),
        _object({"profile": _PROFILE_SCHEMA}, ("profile",)),
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        profile = course_profile_manifest(self.courses.get(context.course_id))
        return ToolResult.success({"profile": profile})


@dataclass(frozen=True, slots=True)
class SourceListTool:
    catalog: RetrievalCatalogPort
    manifest = _manifest(
        "source.list",
        _object({"include_superseded": _BOOL}),
        _object(
            {
                "sources": _array(
                    _object(
                        {
                            "source_id": _TEXT,
                            "revision_id": _TEXT,
                            "title": _TEXT,
                            "kind": {
                                "type": "string",
                                "enum": tuple(item.value for item in SourceKind),
                            },
                            "source_role": _TEXT,
                            "trust_level": _TRUST,
                            "is_current_revision": _BOOL,
                        },
                        (
                            "source_id",
                            "revision_id",
                            "title",
                            "kind",
                            "source_role",
                            "trust_level",
                            "is_current_revision",
                        ),
                    )
                )
            },
            ("sources",),
        ),
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        include = arguments.get("include_superseded", False)
        documents = self.catalog.documents(include_superseded=bool(include))
        seen: set[tuple[str, str]] = set()
        result: list[JsonObject] = []
        for item in sorted(
            documents,
            key=lambda row: (str(row.source_id), str(row.revision_id), str(row.chunk.chunk_id)),
        ):
            key = (str(item.source_id), str(item.revision_id))
            if key in seen or item.course_id != context.course_id:
                continue
            seen.add(key)
            result.append(
                {
                    "source_id": key[0],
                    "revision_id": key[1],
                    "title": item.title,
                    "kind": item.source_kind.value,
                    "source_role": item.source_role,
                    "trust_level": item.trust_level,
                    "is_current_revision": item.is_current_revision,
                }
            )
        return ToolResult.success({"sources": tuple(result)})


@dataclass(frozen=True, slots=True)
class SourceSearchTool:
    retrieval: RetrievalPort
    manifest = _manifest(
        "source.search",
        _object(
            {
                "query": _TEXT,
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "revision_ids": _array(_TEXT, maximum=100),
                "minimum_trust_level": _TRUST,
                "source_kinds": _array(
                    {"type": "string", "enum": tuple(item.value for item in SourceKind)}, maximum=2
                ),
                "source_roles": _array(_TEXT, maximum=100),
                "include_superseded": _BOOL,
            },
            ("query",),
        ),
        output_schema=_object(
            {
                "status": {"type": "string", "enum": ("sufficient", "insufficient", "conflicting")},
                "evidence": _array(
                    _object(
                        {"text": _TEXT, "score": _SCORE, "citation": _CITATION_OUTPUT},
                        ("text", "score", "citation"),
                    )
                ),
                "query_fingerprint": _TEXT,
                "strategy_id": _TEXT,
                "strategy_version": _TEXT,
                "index_version": _TEXT,
                "read_set_fingerprint": _TEXT,
            },
            (
                "status",
                "evidence",
                "query_fingerprint",
                "strategy_id",
                "strategy_version",
                "index_version",
                "read_set_fingerprint",
            ),
        ),
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        limit = cast(int, arguments.get("limit", 8))
        minimum_trust = cast(int, arguments.get("minimum_trust_level", 0))
        revision_ids = cast(tuple[object, ...], arguments.get("revision_ids", ()))
        source_kinds = cast(tuple[object, ...], arguments.get("source_kinds", ()))
        source_roles = cast(tuple[object, ...], arguments.get("source_roles", ()))
        found = self.retrieval.search(
            RetrievalQuery(
                context.course_id,
                str(arguments["query"]),
                limit=limit,
                revision_ids=tuple(RevisionId(str(item)) for item in revision_ids),
                minimum_trust_level=minimum_trust,
                source_kinds=tuple(SourceKind(str(item)) for item in source_kinds),
                source_roles=tuple(str(item) for item in source_roles),
                include_superseded=bool(arguments.get("include_superseded", False)),
            )
        )
        return ToolResult.success(
            {
                "status": found.status.value,
                "evidence": tuple(
                    {
                        "text": item.text,
                        "score": item.score,
                        "citation": _citation_json(item.citation),
                    }
                    for item in found.evidence
                ),
                "query_fingerprint": found.query_fingerprint,
                "strategy_id": found.strategy_id,
                "strategy_version": found.strategy_version,
                "index_version": found.index_version,
                "read_set_fingerprint": found.read_set_fingerprint,
            }
        )


@dataclass(frozen=True, slots=True)
class CitationResolveTool:
    catalog: RetrievalCatalogPort
    content: SourceContentPort
    manifest = _manifest(
        "citation.resolve",
        _object({"citation": _CITATION_INPUT}, ("citation",)),
        _object({"citation": _CITATION_OUTPUT, "text": _TEXT}, ("citation", "text")),
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        raw = cast(Mapping[str, object], arguments["citation"])
        citation = Citation(
            SourceId(str(raw["source_id"])),
            RevisionId(str(raw["revision_id"])),
            ChunkId(str(raw["chunk_id"])),
            cast(int, raw["start_offset"]),
            cast(int, raw["end_offset"]),
            "requested span",
        )
        document = self.catalog.canonical_document(citation.chunk_id)
        if (
            document.course_id != context.course_id
            or document.source_id != citation.source_id
            or document.revision_id != citation.revision_id
        ):
            raise LookupError("citation does not belong to the requested course source")
        resolved = self.content.resolve(citation)
        return ToolResult.success(
            {"citation": _citation_json(resolved.citation), "text": resolved.text}
        )


@dataclass(frozen=True, slots=True)
class SessionGetContextTool:
    sessions: SessionService
    manifest = _manifest(
        "session.get_context", _object({}), _object({"summary_json": _STRING}, ("summary_json",))
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        summary = self.sessions.get_context(context)
        value = None if summary is None else summary_payload(summary)["summary"]
        return ToolResult.success(
            {
                "summary_json": "null"
                if value is None
                else canonical_json_bytes({"summary": value}).decode()
            }
        )


@dataclass(frozen=True, slots=True)
class SessionRecordNoteTool:
    sessions: SessionService
    manifest = _manifest(
        "session.record_note",
        _object({"content": _TEXT}, ("content",)),
        _object(
            {
                "interaction_id": _TEXT,
                "kind": {"type": "string", "enum": ("note",)},
                "content": _TEXT,
            },
            ("interaction_id", "kind", "content"),
        ),
        effect=ToolEffect.CANONICAL_WRITE,
        capability="study:write",
        idempotency=IdempotencyMode.REQUIRED,
        events=("session.interaction_recorded", "session.continuation_summary_updated"),
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        record = self.sessions.record_note(context, str(arguments["content"]))
        return ToolResult.success(
            {"interaction_id": str(record.id), "kind": record.kind.value, "content": record.content}
        )


@dataclass(frozen=True, slots=True)
class GroundingAskTool:
    service: GroundingAskService | GroundingAskServiceProvider
    manifest = _manifest(
        "grounding.ask",
        _object({"question": _TEXT}, ("question",)),
        _object(
            {
                "answer_record_json": _TEXT,
                "events": _array(
                    _object(
                        {
                            "kind": {
                                "type": "string",
                                "enum": tuple(item.value for item in StudyEventKind),
                            },
                            "data": _object(
                                {
                                    "course_id": _TEXT,
                                    "session_id": _TEXT,
                                    "run_id": _TEXT,
                                    "answer_id": _TEXT,
                                },
                                ("course_id", "session_id", "run_id"),
                            ),
                        },
                        ("kind", "data"),
                    )
                ),
            },
            ("answer_record_json", "events"),
        ),
        effect=ToolEffect.ORCHESTRATION,
        capability="study:ask",
        idempotency=IdempotencyMode.REQUIRED,
        events=tuple(item.value for item in StudyEventKind),
    )

    async def invoke(self, arguments: JsonObject, context: ExecutionContext) -> ToolResult:
        service = (
            self.service.resolve()
            if isinstance(self.service, GroundingAskServiceProvider)
            else self.service
        )
        result = await service.ask(str(arguments["question"]), context)
        events = tuple(_study_event(item) for item in result.events)
        record: JsonObject = {
            "id": str(result.answer.id),
            "interaction_id": str(result.answer.interaction_id),
            "question_interaction_id": str(result.answer.question_interaction_id),
            "run_id": str(result.answer.run_id),
            "idempotency_key": result.answer.idempotency_key,
            "command_fingerprint": result.answer.command_fingerprint,
            "answer": grounded_answer_manifest(result.answer.answer),
        }
        return ToolResult.success(
            {
                "answer_record_json": canonical_json_bytes(record).decode(),
                "events": tuple(item.to_json() for item in events),
            },
            events,
        )


def builtin_tools(
    *,
    courses: CourseViewPort,
    catalog: RetrievalCatalogPort,
    retrieval: RetrievalPort,
    content: SourceContentPort,
    sessions: SessionService,
    grounding: GroundingAskService | GroundingAskServiceProvider,
) -> tuple[object, ...]:
    return (
        CourseGetTool(courses),
        SourceListTool(catalog),
        SourceSearchTool(retrieval),
        CitationResolveTool(catalog, content),
        SessionGetContextTool(sessions),
        SessionRecordNoteTool(sessions),
        GroundingAskTool(grounding),
    )


def public_study_tool_manifests() -> tuple[ToolManifest, ...]:
    """Return the exact public manifests without composing repositories or models."""
    manifests = (
        CourseGetTool.manifest,
        SourceListTool.manifest,
        SourceSearchTool.manifest,
        CitationResolveTool.manifest,
        SessionGetContextTool.manifest,
        SessionRecordNoteTool.manifest,
        GroundingAskTool.manifest,
    )
    return tuple(sorted(manifests, key=lambda item: item.name))


def _citation_json(citation: Citation) -> JsonObject:
    if citation.quoted_snippet is None:
        raise ValueError("canonical citations require quoted text")
    return {
        "source_id": str(citation.source_id),
        "revision_id": str(citation.revision_id),
        "chunk_id": str(citation.chunk_id),
        "start_offset": citation.start_offset,
        "end_offset": citation.end_offset,
        "locator": citation.locator,
        "quoted_snippet": citation.quoted_snippet,
    }


def _study_event(event: GroundingStudyEvent) -> StudyEvent:
    kind = StudyEventKind(str(event.kind.value))
    data: dict[str, object] = {
        "course_id": str(event.course_id),
        "session_id": str(event.session_id),
        "run_id": str(event.run_id),
    }
    answer_id = event.answer_id
    if answer_id is not None:
        data["answer_id"] = str(answer_id)
    return StudyEvent(kind, freeze_object(data))  # type: ignore[arg-type]
