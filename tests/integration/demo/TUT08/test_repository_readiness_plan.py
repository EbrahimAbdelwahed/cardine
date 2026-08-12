from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_materials_artifacts_context import (
        COURSE,
        ORIGIN,
        SESSION,
        _context,
        _repository,
    )
else:
    try:
        from tests.integration.demo.TUT08.test_repository_materials_artifacts_context import (
            COURSE,
            ORIGIN,
            SESSION,
            _context,
            _repository,
        )
    except ModuleNotFoundError:
        from test_repository_materials_artifacts_context import (
            COURSE,
            ORIGIN,
            SESSION,
            _context,
            _repository,
        )

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication
from study_agent.domain import (
    PrincipalKind,
    StudyStatementInput,
    StudyStatementKind,
)


def test_repository_plan_and_bootstrap_are_read_only_and_truthful(
    tmp_path: Path,
) -> None:
    root, _revision_id = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    with LocalRepository.open(root) as repository:
        before = len(repository.events.read(COURSE))

    bootstrap = app.get("/api/v1/bootstrap")
    plan = app.get("/api/v1/plan")
    features = cast(dict[str, object], bootstrap["features"])
    counts = cast(dict[str, object], bootstrap["counts"])
    readiness = cast(dict[str, object], plan["readiness"])
    exam = cast(dict[str, object], readiness["exam"])
    exam_sources = cast(dict[str, object], exam["sources"])

    assert features["exam_plan"] is True
    assert counts["pending_proposals"] == 1
    assert plan["status"] == "ready"
    assert readiness["days_remaining"] is None
    assert readiness["deadline_status"] == "missing"
    assert plan["high_water_sequence"] == readiness["high_water_sequence"]
    assert plan["shell_status"] == bootstrap["shell_status"]
    assert {"configured_date", "as_of_date", "deadline_status", "days_remaining"} <= set(
        exam_sources
    )
    assert "readiness_score" not in json.dumps(plan)

    with LocalRepository.open(root) as repository:
        assert len(repository.events.read(COURSE)) == before
    assert app.get("/api/v1/plan") == plan


def test_repository_plan_conflict_is_explicit_and_shell_warns(
    tmp_path: Path,
) -> None:
    root, _revision_id = _repository(tmp_path / "repository")
    with LocalRepository.open(root) as repository:
        sequence = repository.events.read(COURSE)[-1].course_sequence
        first = repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 8, 15)),
            ORIGIN,
            _context("readiness-deadline-1", actor=PrincipalKind.HUMAN),
            sequence,
        )
        repository.study_context_service.record(
            StudyStatementInput(StudyStatementKind.DEADLINE, date(2026, 8, 20)),
            ORIGIN,
            _context("readiness-deadline-2", actor=PrincipalKind.HUMAN),
            first.sequence,
        )

    app = RepositoryUiApplication(root, COURSE, SESSION)
    bootstrap = app.get("/api/v1/bootstrap")
    plan = app.get("/api/v1/plan")
    counts = cast(dict[str, object], bootstrap["counts"])
    readiness = cast(dict[str, object], plan["readiness"])

    assert counts["context_conflicts"] == 1
    assert bootstrap["shell_status"] == "needs_review"
    assert plan["status"] == "conflicted"
    assert readiness["days_remaining"] is None
    assert readiness["deadline_status"] == "conflicted"


def test_browser_plan_uses_server_values_without_date_math() -> None:
    javascript = (
        Path(__file__).parents[4] / "src/cardine/demo/browser.js"
    ).read_text(encoding="utf-8")

    assert 'endpoint: "/api/v1/plan"' in javascript
    assert "function renderPlan(payload)" in javascript
    assert "days_remaining" in javascript
    render_plan = javascript.split("function renderPlan(payload)", 1)[1].split(
        "function renderConflitti(payload)", 1
    )[0]
    assert "new Date(" not in render_plan
    assert "readiness score" in javascript
    assert "function sourceRef(value)" in javascript
    assert 'aria-label="Lavoro aperto oggi"' in javascript


def test_session_read_uses_captured_presentations_not_live_view(tmp_path: Path) -> None:
    root, _revision_id = _repository(tmp_path / "repository")

    class _ForbiddenLiveView:
        def presentations(self, *_args: object) -> object:
            raise AssertionError("session route must not re-read live presentations")

    @contextmanager
    def opener(path: Path, **kwargs: object) -> Iterator[LocalRepository]:
        del kwargs
        with LocalRepository.open(path) as repository:
            repository.tutor_presentations = _ForbiddenLiveView()  # type: ignore[assignment]
            yield repository

    app = RepositoryUiApplication(
        root,
        COURSE,
        SESSION,
        repository_opener=opener,
    )
    session = app.get("/api/v1/session")

    assert session["high_water_sequence"] == app.get("/api/v1/bootstrap")[
        "high_water_sequence"
    ]
