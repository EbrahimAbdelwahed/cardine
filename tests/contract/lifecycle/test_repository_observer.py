from __future__ import annotations

from hashlib import sha256
from pathlib import Path

from study_agent.adapters.filesystem import initialize_local_repository
from study_agent.adapters.filesystem.repository_target import (
    LocalRepositoryPaths,
    RepositoryObservationHandle,
    RepositoryTargetInspectionCode,
    inspect_repository_target,
    resolve_explicit_repository_target,
)
from study_agent.adapters.sqlite import observe_local_repository
from cardine.cli.repository import LocalRepository
from study_agent.domain import (
    CorrelationId,
    CourseId,
    ExecutionContext,
    PrincipalKind,
    SourceId,
)
from study_agent.lifecycle import IndexObservationState, RepositoryObservationState
from study_agent.repository_config import EMPTY_CONFIG
from tests.course_fixtures import canonical_profile


def _tree(root: Path) -> tuple[tuple[str, int, int, str], ...]:
    result: list[tuple[str, int, int, str]] = []
    for path in sorted(root.rglob("*")):
        payload = path.read_bytes() if path.is_file() else b""
        result.append(
            (
                str(path.relative_to(root)),
                path.stat().st_mode,
                path.stat().st_mtime_ns,
                sha256(payload).hexdigest(),
            )
        )
    return tuple(result)


def _context(course_id: CourseId) -> ExecutionContext:
    return ExecutionContext(
        PrincipalKind.SERVICE,
        "lifecycle-observer-test",
        course_id,
        CorrelationId("lifecycle-observer-test"),
    )


def _observation_handle(paths: LocalRepositoryPaths) -> RepositoryObservationHandle:
    target = resolve_explicit_repository_target(paths.root)
    inspection = inspect_repository_target(target, EMPTY_CONFIG)
    assert inspection.code is RepositoryTargetInspectionCode.COMPATIBLE
    assert inspection.observation is not None
    return inspection.observation


def test_observer_replays_canonical_state_and_audits_index_without_writes(
    tmp_path: Path,
) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    course_id = CourseId("course-observed")
    with LocalRepository(paths, EMPTY_CONFIG) as repository:
        repository.course_service.create(canonical_profile(course_id), _context(course_id))
        repository.for_course(course_id).ingestion.ingest(
            filename="notes.md",
            content=b"The aortic valve has three cusps.",
            source_id=SourceId("source-observed"),
            title="Observed notes",
            trust_level=90,
            source_role="primary",
            context=_context(course_id),
        )
        repository.rebuild_retrieval()
    before = _tree(paths.root)

    with _observation_handle(paths) as handle:
        observed = observe_local_repository(handle, EMPTY_CONFIG)

    assert observed.state is RepositoryObservationState.COMPATIBLE
    assert observed.index is not None
    assert observed.index.state is IndexObservationState.HEALTHY
    assert len(observed.courses) == 1
    course = observed.courses[0]
    assert course.profile == canonical_profile(course_id)
    assert course.high_water_sequence == 2
    assert len(course.sources) == 1
    assert course.sources[0].source_id == "source-observed"
    assert course.sources[0].title == "Observed notes"
    assert _tree(paths.root) == before


def test_observer_accepts_initialized_empty_repository_without_creating_databases(
    tmp_path: Path,
) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    before = _tree(paths.root)

    with _observation_handle(paths) as handle:
        observed = observe_local_repository(handle, EMPTY_CONFIG)

    assert observed.state is RepositoryObservationState.COMPATIBLE
    assert observed.courses == ()
    assert observed.index is not None
    assert observed.index.state is IndexObservationState.MISSING
    assert _tree(paths.root) == before


def test_observer_does_not_create_sqlite_wal_or_shared_memory_sidecars(
    tmp_path: Path,
) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    course_id = CourseId("course-no-sidecars")
    with LocalRepository(paths, EMPTY_CONFIG) as repository:
        repository.course_service.create(canonical_profile(course_id), _context(course_id))
        repository.rebuild_retrieval()
    sidecars = tuple(
        Path(f"{database}{suffix}")
        for database in (paths.events, paths.retrieval)
        for suffix in ("-wal", "-shm")
    )
    assert not any(path.exists() for path in sidecars)

    with _observation_handle(paths) as handle:
        observed = observe_local_repository(handle, EMPTY_CONFIG)

    assert observed.state is RepositoryObservationState.COMPATIBLE
    assert not any(path.exists() for path in sidecars)


def test_observer_rejects_rebound_repository_after_handle_creation(
    tmp_path: Path,
) -> None:
    paths = initialize_local_repository(tmp_path / "repository", EMPTY_CONFIG)
    handle = _observation_handle(paths)
    original = tmp_path / "original"
    paths.root.rename(original)
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    marker = attacker / "must-not-read.txt"
    marker.write_text("secret", encoding="utf-8")
    paths.root.symlink_to(attacker, target_is_directory=True)
    try:
        observed = observe_local_repository(handle, EMPTY_CONFIG)
    finally:
        handle.close()

    assert observed.state is RepositoryObservationState.CONFLICT
    assert marker.read_text(encoding="utf-8") == "secret"
