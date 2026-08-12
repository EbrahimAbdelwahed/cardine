"""Pure contract tests for the projection-only study-readiness view."""

from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from cardine.application.study_readiness import StudyReadinessView
from study_agent.domain import CourseId
from study_agent.state import Projection

COURSE = CourseId("course-1")


class _Clock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def now(self) -> datetime:
        return self.value


def _course(*, exam_date: date | None = date(2026, 8, 15)) -> dict[str, Any]:
    return {
        "id": str(COURSE),
        "title": "Anatomy",
        "language": "en",
        "exam_date": exam_date.isoformat() if exam_date is not None else None,
        "assessment_styles": ("single_choice", "free_response"),
        "learning_goals": ("localize landmarks", "explain relations"),
        "source_policy": {"allowed_roles": (), "minimum_trust_level": 0},
        "terminology_policy": {"entries": ()},
    }


def _projection(
    *, sequence: int = 7, exam_date: date | None = date(2026, 8, 15), **state: Any
) -> Projection:
    return Projection(COURSE, sequence, {"course": _course(exam_date=exam_date), **state})


def _view(
    projection: Projection,
    when: datetime = datetime(2026, 8, 1, 12, tzinfo=UTC),
    *,
    recall_available: bool | None = None,
) -> Any:
    return StudyReadinessView(projection, _Clock(when), recall_available=recall_available).get()


def test_clock_must_be_aware_utc() -> None:
    with pytest.raises(ValueError, match="aware"):
        _view(_projection(), datetime(2026, 8, 1, 12))
    with pytest.raises(ValueError, match="UTC"):
        _view(_projection(), datetime(2026, 8, 1, 14, tzinfo=timezone(timedelta(hours=2))))


@pytest.mark.parametrize(
    ("when", "expected"),
    (
        (datetime(2026, 8, 15, 23, 59, tzinfo=UTC), 0),
        (datetime(2026, 8, 14, 23, 59, tzinfo=UTC), 1),
        (datetime(2026, 8, 16, 0, 0, tzinfo=UTC), -1),
    ),
)
def test_days_remaining_is_exact_calendar_difference(when: datetime, expected: int) -> None:
    snapshot = _view(_projection(), when)
    assert snapshot.as_of_date == when.date()
    assert snapshot.exam_date == date(2026, 8, 15)
    assert snapshot.days_remaining == expected
    assert snapshot.deadline_status == "configured"


def test_missing_exam_date_has_explicit_status_and_no_number() -> None:
    snapshot = _view(_projection(exam_date=None))
    assert snapshot.exam_date is None
    assert snapshot.days_remaining is None
    assert snapshot.deadline_status == "missing"


def test_conflicting_learner_deadlines_are_explicit_and_not_numeric() -> None:
    context = {
        "statements": {
            "deadline-1": {
                "statement_id": "deadline-1",
                "course_id": str(COURSE),
                "session_id": "session-1",
                "origin_interaction_id": "interaction-1",
                "kind": "deadline",
                "value": "2026-08-15",
                "status": "active",
                "recorded_at": "2026-07-30T10:00:00+00:00",
            },
            "deadline-2": {
                "statement_id": "deadline-2",
                "course_id": str(COURSE),
                "session_id": "session-1",
                "origin_interaction_id": "interaction-2",
                "kind": "deadline",
                "value": "2026-08-20",
                "status": "active",
                "recorded_at": "2026-07-30T11:00:00+00:00",
            },
        },
        "resolutions": (),
        "commands": {},
    }
    snapshot = _view(_projection(study_context=context))
    assert snapshot.deadline_status == "conflicted"
    assert snapshot.days_remaining is None


def test_sparse_projection_returns_explicit_empty_rows() -> None:
    snapshot = _view(_projection(sequence=1))
    assert snapshot.learning_goals
    assert snapshot.assessment_styles
    assert snapshot.constraints == ()
    assert snapshot.blueprints == ()
    assert snapshot.evidence == ()
    assert snapshot.recall.available is False
    assert snapshot.recall.due_count is None
    assert snapshot.recall.earliest_due_at is None


def test_recall_is_explicitly_unavailable_without_optional_owner() -> None:
    snapshot = _view(_projection(), recall_available=False)
    assert snapshot.recall.available is False
    assert snapshot.recall.due_count is None
    assert snapshot.recall.earliest_due_at is None


def test_recall_available_state_exposes_due_count_without_inference() -> None:
    snapshot = _view(_projection(), recall_available=True)
    assert snapshot.recall.available is True
    assert snapshot.recall.due_count == 0
    assert snapshot.recall.earliest_due_at is None


def test_projection_capture_is_immutable_and_reused_once() -> None:
    projection = _projection()
    snapshot = _view(projection)
    assert snapshot.sequence == projection.sequence
    with pytest.raises((FrozenInstanceError, TypeError, AttributeError)):
        snapshot.learning_goals += ("mutate",)
    with pytest.raises(TypeError):
        snapshot.sources["projection"] = snapshot.source


def test_equivalent_projection_and_clock_are_byte_equivalent() -> None:
    first = _view(_projection(), datetime(2026, 8, 1, 12, tzinfo=UTC))
    second = _view(_projection(), datetime(2026, 8, 1, 12, tzinfo=UTC))
    assert first.to_json() == second.to_json()


def test_source_attribution_is_present_on_every_nonempty_value() -> None:
    snapshot = _view(_projection())
    for value in (*snapshot.learning_goals, *snapshot.assessment_styles):
        assert value.source.projection == "course"
        assert value.source.sequence == snapshot.sequence

    rows = (
        *snapshot.constraints,
        *snapshot.blueprints,
        *snapshot.artifact_counts,
        *snapshot.evidence,
    )
    for row in rows:
        assert row.source.projection
        assert row.source.sequence == snapshot.sequence
    assert snapshot.recall.source.projection == "recall"
    assert snapshot.recall.source.sequence == snapshot.sequence


def test_readiness_does_not_emit_scores_or_plans() -> None:
    payload = _view(_projection()).to_json()
    forbidden = {
        "readiness_score",
        "mastery",
        "retention",
        "coverage",
        "agenda",
        "priority",
        "predicted_score",
    }
    assert forbidden.isdisjoint(payload)
