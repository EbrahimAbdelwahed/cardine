from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import cast

import pytest

from cardine.cli.repository import LocalRepository, initialize_local_repository
from study_agent.domain import CourseId, DomainEvent
from study_agent.ports.scheduling import SchedulingPolicyPort
from study_agent.recall.composition import (
    RecallAvailabilityCode,
    compose_recall,
)
from study_agent.repository_config import EMPTY_CONFIG


class _NoCallEvents:
    def append(
        self,
        course_id: CourseId,
        expected_sequence: int,
        events: object,
    ) -> int:
        del course_id, expected_sequence, events
        raise AssertionError("recall composition must not append")

    def read(
        self,
        course_id: CourseId,
        after_sequence: int = 0,
    ) -> tuple[DomainEvent, ...]:
        del course_id, after_sequence
        return ()


class _NoCallClock:
    def now(self) -> datetime:
        raise AssertionError("clock must not be called during composition")


def test_base_repository_stays_usable_without_optional_recall_extra(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)

    with LocalRepository.open(root) as repository:
        assert repository.recall is None
        assert repository.recall_availability.code is RecallAvailabilityCode.NOT_CONFIGURED
        assert repository.recall_availability.available is False
        assert repository.recall_availability.message.startswith("recall is optional")
        assert repository.course_catalog.list_courses() == ()


def test_scheduler_factory_failure_is_safe_and_does_not_require_provider_imports() -> None:
    composition = compose_recall(
        events=_NoCallEvents(),
        load_projection=lambda course_id: (_ for _ in ()).throw(LookupError(course_id)),
        clock=_NoCallClock(),
        scheduler_factory=lambda: (_ for _ in ()).throw(RuntimeError("missing optional extra")),
    )

    assert composition.service is None
    assert composition.availability.code is RecallAvailabilityCode.UNAVAILABLE
    assert composition.availability.available is False
    assert "install" in composition.availability.message


def test_invalid_scheduler_factory_result_is_typed_unavailable() -> None:
    composition = compose_recall(
        events=_NoCallEvents(),
        load_projection=lambda course_id: (_ for _ in ()).throw(LookupError(course_id)),
        clock=_NoCallClock(),
        scheduler_factory=lambda: cast(SchedulingPolicyPort, object()),
    )

    assert composition.service is None
    assert composition.availability.code is RecallAvailabilityCode.UNAVAILABLE


def test_invalid_explicit_scheduler_is_rejected_at_composition_boundary() -> None:
    with pytest.raises(TypeError, match="must implement SchedulingPolicyPort"):
        compose_recall(
            events=_NoCallEvents(),
            load_projection=lambda course_id: (_ for _ in ()).throw(LookupError(course_id)),
            clock=_NoCallClock(),
            scheduler=cast(SchedulingPolicyPort, object()),
        )


def test_local_repository_rejects_ambiguous_recall_composition(tmp_path: Path) -> None:
    root = tmp_path / "repository"
    initialize_local_repository(root, EMPTY_CONFIG)
    with pytest.raises(TypeError, match="mutually exclusive"):
        def factory() -> SchedulingPolicyPort:
            return cast(SchedulingPolicyPort, object())

        LocalRepository.open(
            root,
            recall_scheduler=cast(SchedulingPolicyPort, object()),
            recall_scheduler_factory=factory,
        )
