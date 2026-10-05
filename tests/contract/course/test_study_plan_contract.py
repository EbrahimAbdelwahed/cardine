from __future__ import annotations

from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from cardine.courses import (
    CourseCommandError,
    CourseConflictError,
    CourseService,
    ProjectionCourseView,
    StudyPlan,
    register_course_events,
)
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.domain import (
    CorrelationId,
    CourseId,
    CourseProfile,
    ExecutionContext,
    PrincipalKind,
    SessionId,
)
from study_agent.state import EventRegistry

COURSE = CourseId("course-plan")


class Clock:
    def now(self) -> datetime:
        return datetime(2026, 10, 5, 8, tzinfo=UTC)


def _context(
    key: str | None = "plan-1",
    *,
    principal: PrincipalKind = PrincipalKind.HUMAN,
    session: SessionId | None = None,
) -> ExecutionContext:
    return ExecutionContext(
        principal,
        "browser",
        COURSE,
        CorrelationId("plan"),
        session_id=session,
        idempotency_key=key,
    )


def _service(tmp_path: Path) -> tuple[CourseService, ProjectionCourseView, SQLiteEventStore]:
    registry = EventRegistry()
    register_course_events(registry)
    events = SQLiteEventStore(tmp_path / "events.sqlite3", registry)
    view = ProjectionCourseView(events.projection)
    service = CourseService(events, Clock(), view)
    service.create(
        CourseProfile(COURSE, "Fisiologia", "it", learning_goals=("Capire",)),
        ExecutionContext(PrincipalKind.SERVICE, "seed", COURSE, CorrelationId("seed")),
    )
    return service, view, events


def test_a_new_course_has_no_plan_and_the_profile_date(tmp_path: Path) -> None:
    _, view, _ = _service(tmp_path)
    assert view.study_plan(COURSE) == StudyPlan(None, None, None)


def test_setting_a_plan_replaces_the_exam_date_and_records_the_rhythm(tmp_path: Path) -> None:
    service, view, _events = _service(tmp_path)
    plan = StudyPlan(date(2027, 2, 15), 90, "Capire, non memorizzare")

    assert service.set_study_plan(plan, _context()) == plan

    assert view.study_plan(COURSE) == plan
    assert view.get(COURSE).exam_date == date(2027, 2, 15)
    later = StudyPlan(date(2027, 3, 1), 60, None)
    service.set_study_plan(later, _context("plan-2"))
    assert view.study_plan(COURSE) == later
    assert view.get(COURSE).exam_date == date(2027, 3, 1)


def test_an_exact_retry_is_idempotent_and_changed_content_conflicts(tmp_path: Path) -> None:
    service, _view, events = _service(tmp_path)
    plan = StudyPlan(date(2027, 2, 15), 90, None)
    service.set_study_plan(plan, _context())
    before = len(tuple(events.read(COURSE)))

    assert service.set_study_plan(plan, _context()) == plan
    assert len(tuple(events.read(COURSE))) == before
    with pytest.raises(CourseConflictError):
        service.set_study_plan(StudyPlan(date(2027, 2, 16), 90, None), _context())


def test_the_plan_is_course_scoped_and_written_by_a_trusted_actor(tmp_path: Path) -> None:
    service, _view, _events = _service(tmp_path)
    plan = StudyPlan(None, 30, None)
    with pytest.raises(CourseCommandError):
        service.set_study_plan(plan, _context(session=SessionId("session-1")))
    with pytest.raises(CourseCommandError):
        service.set_study_plan(plan, _context(principal=PrincipalKind.MODEL))
    with pytest.raises(CourseCommandError):
        service.set_study_plan(plan, _context(None))


@pytest.mark.parametrize(
    ("minutes", "objective"),
    [(4, None), (721, None), (30, " padded "), (30, ""), (30, "x" * 241)],
)
def test_invalid_rhythm_or_objective_is_rejected(minutes: int, objective: str | None) -> None:
    with pytest.raises(ValueError):
        StudyPlan(None, minutes, objective)
