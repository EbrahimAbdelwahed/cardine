from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

if TYPE_CHECKING:
    from tests.integration.demo.TUT08.test_repository_materials_artifacts_context import (
        COURSE,
        SESSION,
        _repository,
    )
else:
    try:
        from tests.integration.demo.TUT08.test_repository_materials_artifacts_context import (
            COURSE,
            SESSION,
            _repository,
        )
    except ModuleNotFoundError:
        from test_repository_materials_artifacts_context import COURSE, SESSION, _repository

from cardine.cli.repository import LocalRepository
from cardine.demo.ui_application import RepositoryUiApplication, UiRequestError


def _command(request_id: str, payload: dict[str, object]) -> dict[str, object]:
    return {
        "schema_version": 1,
        "request_id": request_id,
        "expected_sequence": 0,
        "payload": payload,
    }


def test_a_saved_plan_drives_the_schedule_and_studied_lessons(tmp_path: Path) -> None:
    root, _revision = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    initial = cast(Any, app.get("/api/v1/plan/schedule"))
    assert initial["schedule"]["status"] == "unset"
    # The fixture course is already in use: it is offered the plan, not stopped.
    first_bootstrap = cast(Any, app.get("/api/v1/bootstrap"))
    assert first_bootstrap["onboarding"]["needs_study_plan"] is False
    assert first_bootstrap["today"]["status"] == "unset"
    lessons = [lesson for source in initial["outline"] for lesson in source["lessons"]]
    assert lessons, "every original source contributes at least one lesson"

    with LocalRepository.open(root) as repository:
        today = repository.clock.now().date()
    exam = (today + timedelta(days=12)).isoformat()
    receipt = app.post(
        "/api/v1/plan",
        _command("plan-1", {"exam_date": exam, "daily_minutes": 60, "objective": "Capire"}),
    )
    assert receipt["status"] == "committed"
    assert app.post(
        "/api/v1/plan",
        _command("plan-1", {"exam_date": exam, "daily_minutes": 60, "objective": "Capire"}),
    )["result"] == receipt["result"]

    plan = cast(Any, app.get("/api/v1/plan"))
    assert plan["study_plan"] == {"exam_date": exam, "daily_minutes": 60, "objective": "Capire"}
    assert plan["readiness"]["exam"]["days_remaining"] == 12
    assert "schedule" not in plan, "the plan header stays fast; the schedule loads after it"
    schedule = cast(Any, app.get("/api/v1/plan/schedule"))["schedule"]
    assert schedule["status"] == "ready"
    assert schedule["days"][-1]["date"] == exam
    assert schedule["days"][-1]["kind"] == "exam"
    assert schedule["days"][0]["weekday"] == today.weekday()
    bootstrap = cast(Any, app.get("/api/v1/bootstrap"))
    assert bootstrap["today"]["status"] == "ready"
    assert bootstrap["today"]["days_remaining"] == 12
    assert [item["key"] for item in bootstrap["today"]["lessons"]] == list(
        schedule["days"][0]["lesson_keys"]
    )

    first = lessons[0]
    app.post(
        "/api/v1/student-state",
        _command("studied-1", {"kind": "topic_covered", "topic": first["topic"]}),
    )
    after = cast(Any, app.get("/api/v1/plan/schedule"))
    studied = {
        lesson["key"]: lesson["studied"]
        for source in after["outline"]
        for lesson in source["lessons"]
    }
    assert studied[first["key"]] is True
    assert after["schedule"]["lessons_studied"] == 1
    assert first["key"] not in [
        key for day in after["schedule"]["days"] for key in day["lesson_keys"]
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {"exam_date": "15/02/2027"},
        {"exam_date": None, "daily_minutes": 2},
        {"exam_date": None, "daily_minutes": "60"},
        {"exam_date": None, "unexpected": 1},
    ],
)
def test_invalid_plans_are_rejected_without_writing(
    tmp_path: Path, payload: dict[str, object]
) -> None:
    root, _revision = _repository(tmp_path / "repository")
    app = RepositoryUiApplication(root, COURSE, SESSION)
    with LocalRepository.open(root) as repository:
        before = len(repository.events.read(COURSE))
    with pytest.raises(UiRequestError):
        app.post("/api/v1/plan", _command("bad", payload))
    with LocalRepository.open(root) as repository:
        assert len(repository.events.read(COURSE)) == before
