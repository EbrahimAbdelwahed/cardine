from __future__ import annotations

import asyncio
from pathlib import Path

from cardine.cli.repository import LocalRepository
from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.domain import CorrelationId, CourseId, ExecutionContext, PrincipalKind, SessionId
from study_agent.repository_config import LocalRepositoryConfig


def _context(
    course_id: CourseId,
    *,
    session_id: SessionId | None = None,
    capabilities: frozenset[str],
    key: str,
) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.HUMAN,
        "harness-tool-surface-test",
        course_id,
        CorrelationId(f"harness-surface-{key}"),
        capabilities,
        session_id,
        idempotency_key=key,
    )


def test_cardine_surface_uses_canonical_repository_services(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, LocalRepositoryConfig())
    course_id = CourseId("surface-course")
    session_id = SessionId("surface-session")

    with LocalRepository.open(root) as repository:
        surface = repository.harness_tools()
        names = tuple(item.name for item in surface.manifests)
        assert names == tuple(sorted(names))
        assert {
            "course.create",
            "source.ingest",
            "session.start",
            "context.get",
            "recall.get",
            "artifact.get",
            "assessment.get",
            "evidence.get",
        } <= set(names)

        course = asyncio.run(
            surface.invoke(
                "course.create",
                {
                    "course_id": str(course_id),
                    "title": "Anatomia",
                    "language": "it",
                    "learning_goals": ("Studiare",),
                    "assessment_styles": (),
                },
                _context(course_id, capabilities=frozenset({"course:write"}), key="course"),
            )
        )
        assert course.error is None
        assert repository.courses.get(course_id).title == "Anatomia"

        session = asyncio.run(
            surface.invoke(
                "session.start",
                {"session_id": str(session_id)},
                _context(
                    course_id,
                    session_id=session_id,
                    capabilities=frozenset({"session:write"}),
                    key="session",
                ),
            )
        )
        assert session.error is None

        source = asyncio.run(
            surface.invoke(
                "source.ingest",
                {"filename": "ossa.md", "title": "Ossa", "content": "Il femore è un osso lungo."},
                _context(
                    course_id,
                    session_id=session_id,
                    capabilities=frozenset({"source:write"}),
                    key="source",
                ),
            )
        )
        assert source.error is None
        assert source.value is not None
        chunk_count = source.value["chunk_count"]
        assert isinstance(chunk_count, int)
        assert chunk_count >= 1

        evidence = asyncio.run(
            surface.invoke(
                "evidence.get",
                {},
                _context(
                    course_id,
                    session_id=session_id,
                    capabilities=frozenset({"study:read"}),
                    key="evidence",
                ),
            )
        )
        assert evidence.error is None
        assert evidence.value is not None
        through_sequence = evidence.value["through_sequence"]
        assert isinstance(through_sequence, int)
        assert through_sequence >= 3


def test_surface_rejects_a_missing_grant(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, LocalRepositoryConfig())
    with LocalRepository.open(root) as repository:
        result = asyncio.run(
            repository.harness_tools().invoke(
                "course.create",
                {
                    "course_id": "blocked-course",
                    "title": "Blocked",
                    "language": "en",
                    "learning_goals": (),
                },
                _context(CourseId("blocked-course"), capabilities=frozenset(), key="blocked"),
            )
        )
    assert result.error is not None
    assert result.error.code.value == "unauthorized"
