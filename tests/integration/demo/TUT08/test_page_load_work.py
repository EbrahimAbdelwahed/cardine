"""Deterministic work guards complement the browser's wall-clock budget."""

from pathlib import Path

import pytest

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication
from cardine.knowledge.pageindex_projection import PageIndexProjection
from study_agent.domain import CourseId
from tests.integration.demo.TUT08.test_repository_backed_chat import COURSE, SESSION, _repository


def test_bootstrap_shares_verified_structure_with_indexing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    original = LocalRepository.pageindex_status
    reads = 0

    def statuses(
        repository: LocalRepository, course: CourseId
    ) -> tuple[PageIndexProjection, ...]:
        nonlocal reads
        reads += 1
        return original(repository, course)

    # The structure feeds the summary, the indexing DTO and today's lessons.
    monkeypatch.setattr(LocalRepository, "pageindex_status", statuses)
    payload = app.get("/api/v1/bootstrap")
    assert reads == 1, "bootstrap reread and revalidated the complete source structure"
    assert model.requests == ()
    structure = payload["pageindex"]
    indexing = payload["indexing"]
    assert isinstance(structure, dict) and isinstance(indexing, dict)
    assert indexing["pageindex"]["status"] == structure["status"]
