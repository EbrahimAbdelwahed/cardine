"""Host-authority command handlers for the reference CLI."""

from __future__ import annotations

import signal
import threading
from collections.abc import Mapping
from datetime import date
from hashlib import sha256
from pathlib import Path
from types import FrameType
from uuid import uuid4

from study_agent.adapters.filesystem import FilesystemExportWriter, FilesystemSourceInput
from study_agent.adapters.filesystem.lifecycle import load_lifecycle_manifest
from study_agent.application import ExportService, ExportVersion
from study_agent.courses import course_profile_manifest
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.domain.course import CourseProfile, SourcePolicy, TerminologyPolicy
from study_agent.domain.session import SessionStatus, StudySessionRecord
from study_agent.ingestion.projection import source_manifest
from study_agent.lifecycle import (
    LifecycleAuthority,
    LifecyclePlanV1,
    LifecycleService,
    manifest_schema,
    status_for_plan,
)
from study_agent.operator_skill import extract_skill
from study_agent.repository_config import EMPTY_CONFIG, LocalRepositoryConfig
from study_agent.sessions.events import grounded_answer_manifest

from .lifecycle import LifecyclePlanExpectationError, LocalLifecycleInputs
from .output import CommandOutcome
from .registry import (
    CommandRequest,
    RepositoryRequirement,
    agent_operations_manifest,
    public_study_tool_entries,
    registration_for,
)
from .repository import (
    LocalRepository,
    ModelAdapterRegistry,
    initialize_local_repository,
)

_HOST_PRINCIPAL = "study-agent-cli"


async def execute(
    request: CommandRequest,
    *,
    model_adapters: ModelAdapterRegistry | None = None,
    environment: Mapping[str, str] | None = None,
) -> CommandOutcome:
    """Execute through the production composition root.

    Hosts may explicitly supply technical adapter registrations.  The CLI entry point
    deliberately supplies neither argument, so test or embedding adapters can never
    become an implicit production fallback.
    """
    registration = registration_for(request.name)
    if registration.repository is RepositoryRequirement.NONE:
        result = registration.handler(request, None)
        return result if isinstance(result, CommandOutcome) else await result
    with LocalRepository.open(
        request.repository,
        model_adapters=model_adapters,
        environment=environment,
    ) as repository:
        result = registration.handler(request, repository)
        return result if isinstance(result, CommandOutcome) else await result


def execute_without_repository(request: CommandRequest) -> CommandOutcome:
    """Execute a synchronous repository-free operation without creating an event loop."""
    registration = registration_for(request.name)
    if registration.repository is not RepositoryRequirement.NONE:
        raise RuntimeError("command requires repository-backed execution")
    result = registration.handler(request, None)
    if not isinstance(result, CommandOutcome):
        raise RuntimeError("repository-free command must use a synchronous handler")
    return result


def handle_init(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("init cannot execute through an open repository")
    return _init(request)


async def handle_course_create(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _course_create(_required_repository(repository), request.values)


async def handle_course_list(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _course_list(_required_repository(repository), request.values)


async def handle_source_add(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _source_add(_required_repository(repository), request.values)


async def handle_source_list(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _source_list(_required_repository(repository), request.values)


async def handle_ask(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _ask(_required_repository(repository), request.values)


async def handle_session_list(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _session_list(_required_repository(repository), request.values)


async def handle_session_start(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _session_start(_required_repository(repository), request.values)


async def handle_session_get(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _session_get(_required_repository(repository), request.values)


async def handle_session_resume(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _session_resume(_required_repository(repository), request.values)


async def handle_export(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _export(_required_repository(repository), request.values)


async def handle_doctor(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    return await _doctor(_required_repository(repository), request.values)


def handle_operator_skill(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("operator skill extraction cannot use a repository")
    return CommandOutcome(
        "operator.skill", extract_skill(Path(_text(request.values, "output")))
    )


def handle_manifest_schema(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    del request
    if repository is not None:
        raise RuntimeError("manifest schema cannot use a repository")
    return CommandOutcome("manifest.schema", {"schema": manifest_schema()})


def handle_manifest_validate(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("manifest validation cannot use a repository")
    raw_path = request.values.get("path")
    if not isinstance(raw_path, Path):
        raise ValueError("manifest path is invalid")
    manifest = load_lifecycle_manifest(raw_path)
    return CommandOutcome(
        "manifest.validate",
        {
            "schema_version": manifest.schema_version,
            "manifest_fingerprint": manifest.fingerprint,
            "course_count": len(manifest.courses),
            "source_count": manifest.source_count,
        },
    )


def handle_manifest_plan(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("manifest planning cannot use an open repository")
    return CommandOutcome("manifest.plan", {"plan": _lifecycle_plan(request).to_json()})


def handle_manifest_status(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("manifest status cannot use an open repository")
    plan = _lifecycle_plan(request)
    return CommandOutcome(
        "manifest.status",
        {"status": status_for_plan(plan).to_json(), "plan": plan.to_json()},
    )


def handle_manifest_apply(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("manifest apply cannot use a pre-opened repository")
    inputs = _lifecycle_inputs(request)
    plan = inputs.plan()
    expected = _sha256_text(request.values, "expect_plan")
    if expected != plan.fingerprint:
        raise LifecyclePlanExpectationError(expected, plan)
    receipt = LifecycleService(inputs.manifest, inputs.runtime()).apply(
        plan,
        inputs.snapshots,
        LifecycleAuthority(
            PrincipalKind.SERVICE,
            _HOST_PRINCIPAL,
            CorrelationId(f"lifecycle-plan-sha256:{plan.fingerprint}"),
        ),
    )
    return CommandOutcome("manifest.apply", {"receipt": receipt.to_json()})


def _lifecycle_plan(request: CommandRequest) -> LifecyclePlanV1:
    return _lifecycle_inputs(request).plan()


def _lifecycle_inputs(request: CommandRequest) -> LocalLifecycleInputs:
    raw_path = request.values.get("path")
    if not isinstance(raw_path, Path):
        raise ValueError("manifest path is invalid")
    return LocalLifecycleInputs.load(raw_path)


def handle_describe(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    del request
    if repository is not None:
        raise RuntimeError("describe cannot execute through an open repository")
    return CommandOutcome("describe", agent_operations_manifest())


def handle_tool_list(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    del request
    if repository is not None:
        raise RuntimeError("tool discovery cannot execute through an open repository")
    return CommandOutcome("tool.list", {"tools": public_study_tool_entries()})


def handle_tool_describe(
    request: CommandRequest, repository: LocalRepository | None
) -> CommandOutcome:
    if repository is not None:
        raise RuntimeError("tool discovery cannot execute through an open repository")
    name = _text(request.values, "name")
    try:
        entry = next(
            item
            for item in public_study_tool_entries()
            if _tool_entry_name(item) == name
        )
    except StopIteration as error:
        raise FileNotFoundError(name) from error
    return CommandOutcome("tool.describe", {"tool": entry})


def _required_repository(repository: LocalRepository | None) -> LocalRepository:
    if repository is None:
        raise RuntimeError("command requires an open repository")
    return repository


def _tool_entry_name(entry: JsonObject) -> object:
    manifest = entry.get("manifest")
    return manifest.get("name") if isinstance(manifest, Mapping) else None


def _init(request: CommandRequest) -> CommandOutcome:
    config = request.values.get("config")
    if config is not None and not isinstance(config, LocalRepositoryConfig):
        raise ValueError("configuration is incompatible")
    paths = initialize_local_repository(request.repository, config or EMPTY_CONFIG)
    return CommandOutcome("init", {"repository": str(paths.root.absolute())})


async def _course_create(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    title = _text(values, "title")
    language = _text(values, "language")
    goals = _texts(values, "learning_goals")
    styles = _texts(values, "assessment_styles", required=False)
    raw_exam = values.get("exam_date")
    exam_date = None if raw_exam is None else date.fromisoformat(str(raw_exam))
    course_id = CourseId(
        str(values.get("course_id") or _derived_id("course", title, language, *goals))
    )
    profile = CourseProfile(
        course_id,
        title,
        language,
        exam_date,
        styles,
        goals,
        SourcePolicy(),
        TerminologyPolicy(),
    )
    created = repository.course_service.create(profile, _context(course_id))
    return CommandOutcome("course.create", course_profile_manifest(created))


async def _course_list(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    del values
    courses = tuple(
        course_profile_manifest(profile)
        for profile in repository.course_catalog.list_courses()
    )
    return CommandOutcome("course.list", {"courses": courses})


async def _source_add(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    snapshot = FilesystemSourceInput(repository.paths.root).snapshot_explicit(
        _text(values, "path")
    )
    source_id = SourceId(
        str(values.get("source_id") or _derived_id("source", snapshot.relative_path))
    )
    course = repository.for_course(course_id)
    result = course.ingestion.ingest(
        filename=snapshot.filename,
        content=snapshot.content,
        source_id=source_id,
        title=str(values.get("title") or Path(snapshot.filename).stem),
        trust_level=_integer(values, "trust_level"),
        source_role=_text(values, "source_role"),
        context=_context(course_id),
    )
    canonical: JsonObject = {
        "status": result.status.value,
        "committed": True,
        "committed_sequence": result.committed_sequence,
        "source": source_manifest(result.source),
        "chunk_count": len(result.chunks),
    }
    try:
        receipt = repository.rebuild_retrieval()
    except Exception as error:
        raise SourceIndexError(canonical) from error
    return CommandOutcome(
        "source.add",
        {
            **canonical,
            "index": {
                "indexed_chunks": receipt.indexed_chunks,
                "index_version": receipt.index_version,
                "catalog_fingerprint": receipt.catalog_fingerprint,
            },
        },
    )


async def _source_list(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    repository.courses.get(course_id)
    records = repository.for_course(course_id).content.catalog()
    sources = tuple(
        {
            "source_id": str(item.source.source_id),
            "revision_id": str(item.source.revision_id),
            "title": item.source.title,
            "kind": item.source.kind.value,
            "source_role": item.source.source_role,
            "trust_level": item.source.trust_level,
            "is_current_revision": item.is_current_revision,
            "chunk_count": len(item.chunks),
        }
        for item in records
    )
    return CommandOutcome("source.list", {"course_id": str(course_id), "sources": sources})


async def _ask(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    repository.courses.get(course_id)
    session_value = values.get("session_id")
    session_id = SessionId(str(session_value or f"session-{uuid4()}"))
    key = str(values.get("idempotency_key") or f"ask-{uuid4()}")
    context = _context(
        course_id,
        session_id=session_id,
        idempotency_key=key,
        capabilities=frozenset({"study:ask"}),
    )
    receipt = repository.rebuild_retrieval()
    service = repository.grounding_service(
        course_id, repository.course_index_receipt(course_id, receipt)
    )
    if session_value is not None:
        session = repository.sessions.get_session(course_id, session_id)
        if session.status is not SessionStatus.ACTIVE:
            raise ValueError("ask requires an active session")
    if session_value is None:
        repository.session_service.start(context)
    result = await service.ask(_text(values, "question"), context)
    return CommandOutcome(
        "ask",
        {
            "course_id": str(course_id),
            "session_id": str(session_id),
            "answer_id": str(result.answer.id),
            "run_id": str(result.answer.run_id),
            "answer": grounded_answer_manifest(result.answer.answer),
        },
    )


async def _session_list(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    repository.courses.get(course_id)
    sessions = tuple(
        {
            "session_id": str(item.id),
            "status": item.status.value,
            "started_at": item.started_at.isoformat(),
            "interaction_count": len(item.interaction_ids),
            "answer_count": len(item.run_ids),
        }
        for item in repository.sessions.list_sessions(course_id)
    )
    return CommandOutcome("session.list", {"course_id": str(course_id), "sessions": sessions})


async def _session_start(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    session_id = SessionId(_text(values, "session_id"))
    session = repository.session_service.start(_context(course_id, session_id=session_id))
    return CommandOutcome("session.start", _session_receipt(session))


async def _session_get(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    session_id = SessionId(_text(values, "session_id"))
    return CommandOutcome(
        "session.get",
        _session_receipt(repository.sessions.get_session(course_id, session_id)),
    )


async def _session_resume(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    session_id = SessionId(_text(values, "session_id"))
    session = repository.session_service.resume(_context(course_id, session_id=session_id))
    return CommandOutcome(
        "session.resume",
        {
            "course_id": str(course_id),
            "session_id": str(session.id),
            "status": session.status.value,
        },
    )


def _session_receipt(session: StudySessionRecord) -> JsonObject:
    return {
        "course_id": str(session.course_id),
        "session_id": str(session.id),
        "status": session.status.value,
        "started_at": session.started_at.isoformat(),
        "suspended_at": (
            None if session.suspended_at is None else session.suspended_at.isoformat()
        ),
        "resumed_at": None if session.resumed_at is None else session.resumed_at.isoformat(),
        "ended_at": None if session.ended_at is None else session.ended_at.isoformat(),
        "interaction_count": len(session.interaction_ids),
        "answer_count": len(session.run_ids),
    }


async def _export(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    course_id = CourseId(_text(values, "course_id"))
    output = Path(_text(values, "output"))
    if not output.is_absolute():
        output = repository.paths.root / output
    version_value = values.get("version", ExportVersion.V1.value)
    if not isinstance(version_value, str):
        raise TypeError("version must be text")
    version = ExportVersion(version_value)
    bundle = ExportService(repository.events).assemble(course_id, version=version)
    receipt = FilesystemExportWriter().write(bundle, output)
    return CommandOutcome(
        "export",
        {
            "course_id": str(course_id),
            "destination": str(receipt.destination),
            "manifest_sha256": receipt.manifest_sha256,
            "high_water_sequence": receipt.high_water_sequence,
            "version": version.value,
        },
    )


async def _doctor(repository: LocalRepository, values: dict[str, object]) -> CommandOutcome:
    del values
    courses = repository.course_catalog.list_courses()
    if not all(repository.events.verify_projection(profile.id) for profile in courses):
        raise RuntimeError("event replay does not match a persisted projection")
    repository.rebuild_retrieval()
    return CommandOutcome(
        "doctor",
        {
            "status": "ok",
            "schema_version": repository.config.schema_version,
            "course_count": len(courses),
            "sqlite_fts5": True,
            "event_replay": "ok",
            "retrieval_rebuild": "ok",
            "run_store": "ok",
        },
    )


class SourceIndexError(RuntimeError):
    """Canonical source committed but discardable retrieval rebuild failed."""

    def __init__(self, committed: JsonObject) -> None:
        self.committed = committed
        super().__init__("source committed, but retrieval index rebuild failed")


def _context(
    course_id: CourseId,
    *,
    session_id: SessionId | None = None,
    idempotency_key: str | None = None,
    capabilities: frozenset[str] = frozenset(),
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.HUMAN,
        _HOST_PRINCIPAL,
        course_id,
        CorrelationId(f"correlation-{uuid4()}"),
        capabilities,
        session_id,
        None,
        idempotency_key,
    )


def _derived_id(kind: str, *parts: str) -> str:
    digest = sha256("\0".join(parts).encode()).hexdigest()
    return f"{kind}-sha256:{digest}"


def _text(values: dict[str, object], name: str) -> str:
    value = values.get(name)
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty trimmed text")
    return value


def _texts(values: dict[str, object], name: str, *, required: bool = True) -> tuple[str, ...]:
    value = values.get(name, ())
    if not isinstance(value, (tuple, list)) or any(
        not isinstance(item, str) or not item or item != item.strip() for item in value
    ):
        raise ValueError(f"{name} must contain trimmed text")
    result = tuple(value)
    if required and not result:
        raise ValueError(f"{name} must not be empty")
    return result


def _integer(values: dict[str, object], name: str) -> int:
    value = values.get(name)
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _sha256_text(values: dict[str, object], name: str) -> str:
    value = _text(values, name)
    if len(value) != 64 or any(character not in "0123456789abcdef" for character in value):
        raise ValueError(f"{name} must be a lowercase SHA-256 digest")
    return value


class _DeferredSigint:
    """Narrow non-interruptible region for an automatic-session CLI transaction."""

    def __init__(self, *, enabled: bool) -> None:
        self.enabled = enabled and threading.current_thread() is threading.main_thread()
        self.pending = False
        self._previous: signal._HANDLER | None = None

    def __enter__(self) -> _DeferredSigint:
        if self.enabled:
            self._previous = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGINT, self._handle)
        return self

    def _handle(self, _signum: int, _frame: FrameType | None) -> None:
        self.pending = True

    def __exit__(self, *_: object) -> None:
        if self.enabled and self._previous is not None:
            signal.signal(signal.SIGINT, self._previous)


__all__ = ["CommandRequest", "SourceIndexError", "execute", "execute_without_repository"]
