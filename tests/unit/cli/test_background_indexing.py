from __future__ import annotations

from pathlib import Path

import pytest

from cardine.application.indexing import IndexingPhase, IndexingStatus
from cardine.cli.repository import LocalRepository, initialize_local_repository
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    SourceId,
)
from study_agent.repository_config import LocalRepositoryConfig

COURSE = CourseId("course-indexing")


def _repository(tmp_path: Path) -> Path:
    root = tmp_path / "repository"
    initialize_local_repository(root, LocalRepositoryConfig())
    with LocalRepository.open(root) as repository:
        repository.course_service.create(
            CourseProfile(COURSE, "Indexing", "it", learning_goals=("Studiare",)),
            ExecutionContext(
                PrincipalKind.SERVICE,
                "fixture",
                COURSE,
                CorrelationId("fixture-course"),
            ),
        )
        repository.for_course(COURSE).ingestion.ingest(
            filename="lezioni.md",
            content=b"# Lezione 1\nUno.\n\nDue.\n# Lezione 2\nTre.",
            source_id=SourceId("source-lessons"),
            title="Lezioni",
            trust_level=90,
            source_role="primary",
            context=ExecutionContext(
                PrincipalKind.HUMAN,
                "fixture",
                COURSE,
                CorrelationId("fixture-source"),
            ),
        )
        queued = repository.queue_indexing()
        assert queued.status is IndexingStatus.QUEUED
        assert queued.phase is IndexingPhase.QUEUED
    return root


def test_indexing_queue_survives_restart_and_reconciles_to_searchable_state(
    tmp_path: Path,
) -> None:
    root = _repository(tmp_path)

    with LocalRepository.open(root) as repository:
        queued = repository.indexing_status()
        assert queued is not None
        assert queued.status is IndexingStatus.QUEUED
        terminal = repository.reconcile_indexing()
        assert terminal.status in {IndexingStatus.READY, IndexingStatus.DEGRADED}
        assert terminal.phase is IndexingPhase.COMPLETE
        assert terminal.indexed_chunks > 0
        assert repository.search_lessons(COURSE, "Lezione 1").candidates

    with LocalRepository.open(root) as restarted:
        assert restarted.indexing_status() == terminal


def test_structural_failure_is_persisted_as_terminal_failed_state(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repository(tmp_path)

    with LocalRepository.open(root) as repository:
        def fail_structure(_course_id: CourseId) -> int:
            raise OSError("fixture structural failure")

        monkeypatch.setattr(repository, "reconcile_pageindex", fail_structure)
        terminal = repository.reconcile_indexing()

        assert terminal.status is IndexingStatus.FAILED
        assert terminal.phase is IndexingPhase.COMPLETE
        assert terminal.error_code == "structural_index_failed"

    with LocalRepository.open(root) as restarted:
        assert restarted.indexing_status() == terminal
