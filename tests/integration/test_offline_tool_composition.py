from __future__ import annotations

import asyncio
import socket
import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import NoReturn, cast

import pytest

from cardine.application.grounding_ask import GroundingAskService
from cardine.cli.config import EMPTY_CONFIG
from cardine.cli.repository import LocalRepository, initialize_local_repository
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SessionId,
    SourceId,
)
from study_agent.domain._validation import JsonObject
from study_agent.ports import IndexReceipt
from study_agent.tools import GroundingAskTool, ToolErrorCode
from study_agent.tools.builtin import GroundingAskServiceProvider
from tests.course_fixtures import canonical_profile


class _ExplodingEnvironment(Mapping[str, str]):
    """Prove offline composition does not enumerate or resolve credentials."""

    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"credential access is forbidden during offline composition: {key}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("credential enumeration is forbidden during offline composition")

    def __len__(self) -> int:
        raise AssertionError("credential enumeration is forbidden during offline composition")


def _context(
    course_id: CourseId,
    *capabilities: str,
    session_id: SessionId | None = None,
    key: str | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "offline-composition-test",
        course_id,
        CorrelationId(f"correlation-{key or 'read'}"),
        frozenset(capabilities),
        session_id,
        idempotency_key=key,
    )


def _run(
    repository_root: Path, course_id: CourseId, monkeypatch: pytest.MonkeyPatch
) -> None:
    def reject_outbound_socket(*args: object, **kwargs: object) -> NoReturn:
        raise AssertionError("outbound sockets are forbidden during offline composition")

    monkeypatch.setattr(socket, "create_connection", reject_outbound_socket)
    with LocalRepository.open(
        repository_root, environment=_ExplodingEnvironment()
    ) as repository:
        course = repository.for_course(course_id)
        repository.course_service.create(
            canonical_profile(course_id), _context(course_id)
        )
        course.ingestion.ingest(
            filename="cardiology.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("source-cardiology"),
            title="Cardiology",
            trust_level=100,
            source_role="reference",
            context=_context(course_id),
        )
        repository.rebuild_retrieval()
        session_id = SessionId("session-offline")
        repository.session_service.start(
            _context(course_id, session_id=session_id)
        )

        def unexpected_rebuild() -> IndexReceipt:
            raise AssertionError("registry access must not rebuild retrieval")

        monkeypatch.setattr(repository, "rebuild_retrieval", unexpected_rebuild)
        registry = repository.study_tools(course_id)
        assert tuple(item.name for item in registry.manifests) == (
            "citation.resolve",
            "course.get",
            "grounding.ask",
            "session.get_context",
            "session.record_note",
            "source.list",
            "source.search",
        )

        read = _context(course_id, "study:read", session_id=session_id)
        course_result = asyncio.run(registry.invoke("course.get", {}, read))
        source_list = asyncio.run(registry.invoke("source.list", {}, read))
        source_search = asyncio.run(
            registry.invoke("source.search", {"query": "aortic valve"}, read)
        )
        session_context = asyncio.run(
            registry.invoke("session.get_context", {}, read)
        )
        assert course_result.error is None
        assert source_list.error is None
        assert source_search.error is None
        assert session_context.error is None

        assert source_search.value is not None
        evidence = cast(tuple[JsonObject, ...], source_search.value["evidence"])
        citation = cast(JsonObject, evidence[0]["citation"])
        citation_input = {
            key: citation[key]
            for key in (
                "source_id",
                "revision_id",
                "chunk_id",
                "start_offset",
                "end_offset",
            )
        }
        resolved = asyncio.run(
            registry.invoke("citation.resolve", {"citation": citation_input}, read)
        )
        assert resolved.error is None
        assert resolved.value is not None
        assert resolved.value["text"] == "The aortic valve has three cusps."

        write = _context(
            course_id,
            "study:write",
            session_id=session_id,
            key="offline-note-1",
        )
        note = asyncio.run(
            registry.invoke("session.record_note", {"content": "Review valves"}, write)
        )
        assert note.error is None

        events_before_ask = tuple(repository.events.read(course_id))
        with sqlite3.connect(repository.paths.runs) as connection:
            runs_before_ask = connection.execute(
                "SELECT COUNT(*) FROM playbook_runs"
            ).fetchone()[0]
        ask = asyncio.run(
            registry.invoke(
                "grounding.ask",
                {"question": "How many cusps?"},
                _context(
                    course_id,
                    "study:ask",
                    session_id=session_id,
                    key="offline-ask-1",
                ),
            )
        )
        assert ask.error is not None
        assert ask.error.code is ToolErrorCode.INCOMPATIBLE_RUNTIME
        assert tuple(repository.events.read(course_id)) == events_before_ask
        with sqlite3.connect(repository.paths.runs) as connection:
            assert (
                connection.execute("SELECT COUNT(*) FROM playbook_runs").fetchone()[0]
                == runs_before_ask
            )


def test_offline_registry_composes_and_only_grounding_requires_a_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "offline-repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    _run(root, CourseId("course-offline"), monkeypatch)


class _EagerServiceUsed(RuntimeError):
    pass


class _CallableEagerGroundingService:
    def __call__(self) -> NoReturn:
        raise AssertionError("an eager grounding service must not be invoked as a factory")

    async def ask(self, question: str, context: ExecutionContext) -> NoReturn:
        raise _EagerServiceUsed


def test_callable_eager_grounding_service_is_not_treated_as_a_provider() -> None:
    service = cast(GroundingAskService, _CallableEagerGroundingService())
    tool = GroundingAskTool(service)

    with pytest.raises(_EagerServiceUsed):
        asyncio.run(
            tool.invoke(
                {"question": "How many cusps?"},
                _context(CourseId("course-eager"), "study:ask"),
            )
        )


def test_lazy_grounding_provider_resolves_once_across_repeated_tool_uses() -> None:
    resolutions = 0
    service = cast(GroundingAskService, _CallableEagerGroundingService())

    def resolve() -> GroundingAskService:
        nonlocal resolutions
        resolutions += 1
        return service

    tool = GroundingAskTool(GroundingAskServiceProvider(resolve))
    context = _context(CourseId("course-lazy"), "study:ask")

    for _ in range(2):
        with pytest.raises(_EagerServiceUsed):
            asyncio.run(tool.invoke({"question": "How many cusps?"}, context))

    assert resolutions == 1
