"""Deterministic work guards complement the browser's wall-clock budget."""

from pathlib import Path

import pytest

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain import CourseId
from study_agent.domain._validation import JsonObject
from tests.integration.demo.TUT08.test_repository_backed_chat import COURSE, SESSION, _repository


def test_bootstrap_shares_verified_structure_with_indexing_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    root, adapters, model = _repository(tmp_path)
    app = RepositoryUiApplication(root, COURSE, SESSION, model_adapters=adapters)
    original = LocalRepository.pageindex_summary
    reads = 0

    def summary(repository: LocalRepository, course: CourseId) -> JsonObject:
        nonlocal reads
        reads += 1
        return original(repository, course)

    monkeypatch.setattr(LocalRepository, "pageindex_summary", summary)
    payload = app.get("/api/v1/bootstrap")
    assert reads == 1, "bootstrap reread and revalidated the complete source structure"
    assert model.requests == ()
    structure = payload["pageindex"]
    indexing = payload["indexing"]
    assert isinstance(structure, dict) and isinstance(indexing, dict)
    assert indexing["pageindex"]["status"] == structure["status"]
