from __future__ import annotations

import builtins
import json
import os
import socket
import subprocess
import sys
from hashlib import sha256
from pathlib import Path

import pytest

from cardine.domain.course import CourseProfile
from study_agent.domain.identifiers import CourseId
from study_agent.domain.source import SourceKind
from study_agent.lifecycle import (
    DesiredCourse,
    DesiredRepository,
    DesiredSource,
    IndexObservationState,
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
from study_agent.repository_config import (
    LocalRepositoryConfig,
    ModelAdapterConfig,
)


def _source(
    source_id: str = "source-a",
    path: str = "notes.md",
    *,
    title: str | None = "Notes",
    trust_level: int = 90,
) -> DesiredSource:
    return DesiredSource(source_id, path, title, trust_level, "lecture")


def _course(
    course_id: str = "course-a",
    *,
    title: str = "Anatomy",
    sources: tuple[DesiredSource, ...] | None = None,
) -> DesiredCourse:
    return DesiredCourse(
        course_id,
        title,
        "en",
        None,
        ("Understand anatomy",),
        (),
        (_source(),) if sources is None else sources,
    )


def _manifest(
    *courses: DesiredCourse,
    model: ModelAdapterConfig | None = None,
) -> LifecycleManifestV1:
    return LifecycleManifestV1(
        DesiredRepository("repository", model),
        courses or (_course(),),
    )


def _snapshot(path: str = "notes.md", content: bytes = b"# Notes\n") -> SourceSnapshot:
    return SourceSnapshot(path, content, sha256(content).hexdigest(), len(content))


def _profile(course_id: str = "course-a", *, title: str = "Anatomy") -> CourseProfile:
    return CourseProfile(
        CourseId(course_id),
        title,
        "en",
        learning_goals=("Understand anatomy",),
    )


def _observed_source(
    source_id: str = "source-a",
    *,
    path: str = "notes.md",
    content: bytes = b"# Notes\n",
    title: str = "Notes",
    trust_level: int = 90,
) -> ObservedSource:
    snapshot = _snapshot(path, content)
    return ObservedSource(
        source_id,
        f"revision-{source_id}",
        SourceKind.MARKDOWN,
        title,
        trust_level,
        "lecture",
        snapshot.checksum_sha256,
        snapshot.byte_size,
    )


def _observed_course(
    course_id: str = "course-a",
    *,
    title: str = "Anatomy",
    high_water: int = 2,
    sources: tuple[ObservedSource, ...] | None = None,
) -> ObservedCourse:
    return ObservedCourse(
        _profile(course_id, title=title),
        high_water,
        (_observed_source(),) if sources is None else sources,
    )


def _observation(
    *courses: ObservedCourse,
    index: IndexObservationState | None = IndexObservationState.HEALTHY,
    config: LocalRepositoryConfig | None = None,
) -> RepositoryObservation:
    return RepositoryObservation(
        RepositoryObservationState.COMPATIBLE,
        LocalRepositoryConfig(None) if config is None else config,
        courses or (_observed_course(),),
        None if index is None else ObservedIndex(index),
    )


def _actions(plan: object) -> list[dict[str, object]]:
    raw = plan.to_json()["actions"]  # type: ignore[attr-defined]
    normalized: list[dict[str, object]] = []
    for action in raw:
        item = dict(action)
        if item["desired_fingerprint"] is not None:
            digest = item["desired_fingerprint"]
            assert isinstance(digest, str) and len(digest) == 64
            item["desired_fingerprint"] = "<sha256>"
        normalized.append(item)
    return normalized


def _action(
    ordinal: int,
    kind: str,
    owner: str,
    code: str,
    *,
    course_id: str | None = None,
    source_id: str | None = None,
    expected_high_water: int | None = None,
    fingerprint: bool = False,
) -> dict[str, object]:
    return {
        "code": code,
        "course_id": course_id,
        "desired_fingerprint": "<sha256>" if fingerprint else None,
        "expected_high_water": expected_high_water,
        "kind": kind,
        "ordinal": ordinal,
        "owner": owner,
        "source_id": source_id,
    }


def test_absent_repository_has_a_single_initialization_action() -> None:
    plan = plan_lifecycle(
        _manifest(),
        (_snapshot(),),
        RepositoryObservation(RepositoryObservationState.ABSENT),
    )

    assert _actions(plan) == [
        _action(
            0,
            "initialize",
            "repository",
            "repository_absent",
            fingerprint=True,
        )
    ]
    assert status_for_plan(plan).kind is LifecycleStatusKind.CANONICAL_DRIFT


def test_converged_plan_has_only_explicit_noops() -> None:
    plan = plan_lifecycle(_manifest(), (_snapshot(),), _observation())

    assert _actions(plan) == [
        _action(
            0,
            "noop",
            "repository",
            "repository_compatible",
            fingerprint=True,
        ),
        _action(
            1,
            "noop",
            "course",
            "course_converged",
            course_id="course-a",
            expected_high_water=2,
            fingerprint=True,
        ),
        _action(
            2,
            "noop",
            "source",
            "source_converged",
            course_id="course-a",
            source_id="source-a",
            expected_high_water=2,
            fingerprint=True,
        ),
        _action(3, "noop", "index", "index_healthy"),
    ]
    assert status_for_plan(plan).kind is LifecycleStatusKind.CONVERGED


@pytest.mark.parametrize(
    ("observed_source", "snapshot"),
    [
        (_observed_source(content=b"old bytes"), _snapshot(content=b"new bytes")),
        (_observed_source(title="Old title"), _snapshot()),
    ],
    ids=("changed-bytes", "metadata-drift"),
)
def test_source_drift_plans_one_revision_before_index_work(
    observed_source: ObservedSource,
    snapshot: SourceSnapshot,
) -> None:
    plan = plan_lifecycle(
        _manifest(),
        (snapshot,),
        _observation(
            _observed_course(sources=(observed_source,)),
            index=IndexObservationState.STALE,
        ),
    )

    assert _actions(plan)[2:] == [
        _action(
            2,
            "ingest_revision",
            "source",
            "source_changed",
            course_id="course-a",
            source_id="source-a",
            expected_high_water=2,
            fingerprint=True,
        ),
        _action(3, "rebuild_index", "index", "canonical_state_changed"),
    ]
    assert status_for_plan(plan).kind is LifecycleStatusKind.SOURCE_DRIFT


def test_missing_course_is_created_before_its_first_revision() -> None:
    empty = RepositoryObservation(
        RepositoryObservationState.COMPATIBLE,
        LocalRepositoryConfig(None),
        (),
        ObservedIndex(IndexObservationState.HEALTHY),
    )
    plan = plan_lifecycle(_manifest(), (_snapshot(),), empty)
    assert _actions(plan)[1:3] == [
        _action(
            1,
            "create_course",
            "course",
            "course_absent",
            course_id="course-a",
            expected_high_water=0,
            fingerprint=True,
        ),
        _action(
            2,
            "ingest_revision",
            "source",
            "source_absent",
            course_id="course-a",
            source_id="source-a",
            expected_high_water=1,
            fingerprint=True,
        ),
    ]
    assert _actions(plan)[-1] == _action(
        3, "rebuild_index", "index", "canonical_state_changed"
    )
    assert status_for_plan(plan).kind is LifecycleStatusKind.CANONICAL_DRIFT


def test_source_change_rebuilds_an_index_that_is_healthy_before_the_mutation() -> None:
    plan = plan_lifecycle(
        _manifest(),
        (_snapshot(content=b"new bytes"),),
        _observation(
            _observed_course(sources=(_observed_source(content=b"old bytes"),)),
            index=IndexObservationState.HEALTHY,
        ),
    )

    assert _actions(plan)[-1] == _action(
        3, "rebuild_index", "index", "canonical_state_changed"
    )


def test_immutable_course_profile_drift_is_a_conflict_and_blocks_descendants() -> None:
    plan = plan_lifecycle(
        _manifest(),
        (_snapshot(),),
        _observation(_observed_course(title="Old Anatomy")),
    )

    assert _actions(plan)[1:] == [
        _action(
            1,
            "conflict",
            "course",
            "course_profile_conflict",
            course_id="course-a",
            expected_high_water=2,
            fingerprint=True,
        )
    ]
    assert status_for_plan(plan).kind is LifecycleStatusKind.CANONICAL_CONFLICT


@pytest.mark.parametrize(
    ("index", "code"),
    [
        (None, "index_missing"),
        (IndexObservationState.MISSING, "index_missing"),
        (IndexObservationState.STALE, "index_stale"),
    ],
)
def test_discardable_index_degradation_is_distinct_from_canonical_drift(
    index: IndexObservationState | None,
    code: str,
) -> None:
    plan = plan_lifecycle(_manifest(), (_snapshot(),), _observation(index=index))

    assert _actions(plan)[-1] == _action(3, "rebuild_index", "index", code)
    assert status_for_plan(plan).kind is LifecycleStatusKind.OPERATIONAL_DEGRADATION


def test_repository_config_mismatch_is_a_root_conflict() -> None:
    desired_model = ModelAdapterConfig("generic", {"model": "cheap"}, "MODEL_KEY")
    observed_model = ModelAdapterConfig("generic", {"model": "different"}, "MODEL_KEY")
    plan = plan_lifecycle(
        _manifest(model=desired_model),
        (_snapshot(),),
        _observation(config=LocalRepositoryConfig(observed_model)),
    )

    assert _actions(plan) == [
        _action(
            0,
            "conflict",
            "repository",
            "repository_config_conflict",
            fingerprint=True,
        )
    ]
    assert status_for_plan(plan).kind is LifecycleStatusKind.CANONICAL_CONFLICT


def test_removals_are_warning_only_and_never_plan_deletion() -> None:
    current = _observed_course(
        sources=(
            _observed_source(),
            _observed_source("source-z", path="extra.md"),
        )
    )
    extra = _observed_course("course-z", sources=())
    plan = plan_lifecycle(_manifest(), (_snapshot(),), _observation(current, extra))

    warnings = [item for item in _actions(plan) if item["kind"] == "warning"]
    assert warnings == [
        _action(
            2,
            "warning",
            "course",
            "course_removed_from_manifest_ignored",
            course_id="course-z",
            expected_high_water=2,
        ),
        _action(
            4,
            "warning",
            "source",
            "source_removed_from_manifest_ignored",
            course_id="course-a",
            source_id="source-z",
            expected_high_water=2,
        ),
    ]
    assert not any(item["kind"] == "delete" for item in _actions(plan))
    assert status_for_plan(plan).kind is LifecycleStatusKind.CONVERGED


def test_action_order_is_repository_courses_sources_then_index() -> None:
    manifest = _manifest(
        _course("course-z", sources=(_source("source-z", "z.md"),)),
        _course("course-a", sources=(_source("source-b", "b.md"), _source("source-a"))),
    )
    snapshots = (_snapshot(), _snapshot("b.md", b"B"), _snapshot("z.md", b"Z"))
    observed = RepositoryObservation(
        RepositoryObservationState.COMPATIBLE,
        LocalRepositoryConfig(None),
        (),
        ObservedIndex(IndexObservationState.MISSING),
    )
    plan = plan_lifecycle(manifest, snapshots, observed)

    assert [
        (item["owner"], item["course_id"], item["source_id"])
        for item in _actions(plan)
    ] == [
        ("repository", None, None),
        ("course", "course-a", None),
        ("course", "course-z", None),
        ("source", "course-a", "source-a"),
        ("source", "course-a", "source-b"),
        ("source", "course-z", "source-z"),
        ("index", None, None),
    ]


def test_plan_bytes_and_fingerprint_are_stable_in_a_fresh_python_process() -> None:
    plan = plan_lifecycle(_manifest(), (_snapshot(),), _observation())
    program = """
from hashlib import sha256
from cardine.domain.course import CourseProfile
from study_agent.domain.identifiers import CourseId
from study_agent.domain.source import SourceKind
from study_agent.lifecycle import *
from study_agent.ports.source_input import SourceSnapshot
from study_agent.repository_config import LocalRepositoryConfig
content = b"# Notes\\n"
snapshot = SourceSnapshot("notes.md", content, sha256(content).hexdigest(), len(content))
manifest = LifecycleManifestV1(
    DesiredRepository("repository", None),
    (DesiredCourse("course-a", "Anatomy", "en", None, ("Understand anatomy",), (),
        (DesiredSource("source-a", "notes.md", "Notes", 90, "lecture"),)),),
)
profile = CourseProfile(CourseId("course-a"), "Anatomy", "en",
    learning_goals=("Understand anatomy",))
source = ObservedSource("source-a", "revision-source-a", SourceKind.MARKDOWN,
    "Notes", 90, "lecture", snapshot.checksum_sha256, snapshot.byte_size)
observed = RepositoryObservation(RepositoryObservationState.COMPATIBLE,
    LocalRepositoryConfig(None), (ObservedCourse(profile, 2, (source,)),),
    ObservedIndex(IndexObservationState.HEALTHY))
plan = plan_lifecycle(manifest, (snapshot,), observed)
print(json.dumps({"bytes": plan.canonical_bytes().hex(), "fingerprint": plan.fingerprint}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", "import json\n" + program],
        check=True,
        capture_output=True,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[3] / "src")},
        text=True,
    )

    fresh = json.loads(completed.stdout)
    assert bytes.fromhex(fresh["bytes"]) == plan.canonical_bytes()
    assert fresh["fingerprint"] == plan.fingerprint


def test_planning_uses_no_filesystem_or_network_capability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("pure planning attempted external I/O")

    monkeypatch.setattr(builtins, "open", denied)
    monkeypatch.setattr(Path, "open", denied)
    monkeypatch.setattr(Path, "read_bytes", denied)
    monkeypatch.setattr(Path, "write_bytes", denied)
    monkeypatch.setattr(socket, "socket", denied)
    monkeypatch.setattr(socket, "create_connection", denied)

    plan = plan_lifecycle(_manifest(), (_snapshot(),), _observation())

    assert status_for_plan(plan).kind is LifecycleStatusKind.CONVERGED


@pytest.mark.parametrize(
    "snapshots",
    [
        (),
        (_snapshot(), _snapshot()),
        (_snapshot("other.md"),),
    ],
    ids=("missing", "extra", "wrong-path"),
)
def test_snapshot_cardinality_and_paths_must_match_manifest_exactly(
    snapshots: tuple[SourceSnapshot, ...],
) -> None:
    expected = "1:1" if len(snapshots) != 1 else "order and paths"
    with pytest.raises(ValueError, match=expected):
        plan_lifecycle(_manifest(), snapshots, _observation())
