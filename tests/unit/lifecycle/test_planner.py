from __future__ import annotations

from hashlib import sha256

import pytest

from cardine.domain.course import CourseProfile
from study_agent.domain.identifiers import CourseId
from study_agent.domain.source import SourceKind
from study_agent.lifecycle import (
    DesiredCourse,
    DesiredRepository,
    DesiredSource,
    IndexObservationState,
    LifecycleActionKind,
    LifecycleManifestV1,
    LifecycleStatusKind,
    ObservedCourse,
    ObservedIndex,
    ObservedSource,
    RepositoryObservation,
    RepositoryObservationState,
    plan_lifecycle,
    status_for_plan,
)
from study_agent.ports.source_input import SourceSnapshot
from study_agent.repository_config import LocalRepositoryConfig


def _manifest(*, title: str | None = "Notes") -> LifecycleManifestV1:
    return LifecycleManifestV1(
        DesiredRepository("repository", None),
        (
            DesiredCourse(
                "course-a",
                "Anatomy",
                "en",
                None,
                ("Understand anatomy",),
                (),
                (DesiredSource("source-a", "notes.md", title, 90, "lecture"),),
            ),
        ),
    )


def _snapshot() -> SourceSnapshot:
    content = b"# Notes\n"
    return SourceSnapshot("notes.md", content, sha256(content).hexdigest(), len(content))


def _profile() -> CourseProfile:
    return CourseProfile(
        CourseId("course-a"),
        "Anatomy",
        "en",
        learning_goals=("Understand anatomy",),
    )


def _source(*, title: str = "Notes") -> ObservedSource:
    snapshot = _snapshot()
    return ObservedSource(
        "source-a",
        "revision-sha256:" + "a" * 64,
        SourceKind.MARKDOWN,
        title,
        90,
        "lecture",
        snapshot.checksum_sha256,
        snapshot.byte_size,
    )


def _observed(*, source: ObservedSource | None = None) -> RepositoryObservation:
    return RepositoryObservation(
        RepositoryObservationState.COMPATIBLE,
        LocalRepositoryConfig(None),
        (ObservedCourse(_profile(), 2, ((_source() if source is None else source),)),),
        ObservedIndex(IndexObservationState.HEALTHY),
    )


def test_absent_repository_plans_initialization_only() -> None:
    plan = plan_lifecycle(
        _manifest(),
        (_snapshot(),),
        RepositoryObservation(RepositoryObservationState.ABSENT),
    )

    assert tuple(action.kind for action in plan.actions) == (LifecycleActionKind.INITIALIZE,)
    assert status_for_plan(plan).kind is LifecycleStatusKind.CANONICAL_DRIFT


def test_converged_plan_is_stable_and_contains_only_noops() -> None:
    first = plan_lifecycle(_manifest(), (_snapshot(),), _observed())
    second = plan_lifecycle(_manifest(), (_snapshot(),), _observed())

    assert first.canonical_bytes() == second.canonical_bytes()
    assert first.fingerprint == second.fingerprint
    assert all(action.kind is LifecycleActionKind.NOOP for action in first.actions)
    assert status_for_plan(first).kind is LifecycleStatusKind.CONVERGED


def test_metadata_drift_ingests_before_index_rebuild() -> None:
    observed = RepositoryObservation(
        RepositoryObservationState.COMPATIBLE,
        LocalRepositoryConfig(None),
        (ObservedCourse(_profile(), 2, (_source(title="Old title"),)),),
        ObservedIndex(IndexObservationState.STALE),
    )

    plan = plan_lifecycle(_manifest(), (_snapshot(),), observed)
    mutations = tuple(
        action for action in plan.actions if action.kind is not LifecycleActionKind.NOOP
    )

    assert tuple(action.kind for action in mutations) == (
        LifecycleActionKind.INGEST_REVISION,
        LifecycleActionKind.REBUILD_INDEX,
    )
    assert mutations[1].code == "canonical_state_changed"
    assert mutations[0].expected_high_water == 2
    assert mutations[1].expected_high_water is None
    assert status_for_plan(plan).kind is LifecycleStatusKind.SOURCE_DRIFT


def test_snapshots_are_strict_positional_inputs() -> None:
    content = b"text"
    wrong = SourceSnapshot("other.md", content, sha256(content).hexdigest(), len(content))

    with pytest.raises(ValueError, match="order and paths"):
        plan_lifecycle(_manifest(), (wrong,), _observed())
