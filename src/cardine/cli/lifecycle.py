"""Offline lifecycle composition over the existing local repository services."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from study_agent.adapters.filesystem import FilesystemSourceInput
from study_agent.adapters.filesystem.lifecycle import load_lifecycle_manifest
from study_agent.adapters.filesystem.repository_target import (
    RepositoryTargetError,
    RepositoryTargetInspectionCode,
    initialize_repository_target,
    inspect_repository_target,
    resolve_repository_target,
)
from study_agent.adapters.sqlite import (
    SQLiteConnectionIdentityError,
    observe_local_repository,
)
from study_agent.courses import CourseConflictError, RetryableCourseConflictError
from study_agent.domain import SourceId
from study_agent.domain.context import ExecutionContext
from study_agent.ingestion import IngestionErrorCode, TextIngestionError
from study_agent.lifecycle import (
    DesiredCourse,
    DesiredSource,
    LifecycleManifestV1,
    LifecyclePlanV1,
    LifecycleRuntimeConflictError,
    LifecycleRuntimeIndexError,
    RepositoryObservation,
    RepositoryObservationState,
    desired_course_profile,
    desired_repository_config,
    desired_source_title,
    plan_lifecycle,
)
from study_agent.ports import SourceSnapshot
from study_agent.repository_config import LocalRepositoryConfig
from study_agent.retrieval import SourceContentError

from .repository import LocalRepository, LocalRepositoryError


class LifecyclePlanExpectationError(RuntimeError):
    """The caller-authorized plan no longer matches current local inputs."""

    def __init__(self, expected: str, observed: LifecyclePlanV1) -> None:
        self.expected = expected
        self.observed = observed
        super().__init__("authorized lifecycle plan no longer matches current state")


@dataclass(frozen=True, slots=True)
class LocalLifecycleInputs:
    """One captured manifest/source input set shared by plan, status, and apply."""

    manifest_root: Path
    manifest: LifecycleManifestV1
    snapshots: tuple[SourceSnapshot, ...]

    @classmethod
    def load(cls, path: Path) -> LocalLifecycleInputs:
        manifest = load_lifecycle_manifest(path)
        manifest_root = path.expanduser().absolute().parent
        source_paths = tuple(
            source.path for course in manifest.courses for source in course.sources
        )
        snapshots = FilesystemSourceInput(manifest_root).snapshots(source_paths)
        return cls(manifest_root, manifest, snapshots)

    def runtime(self) -> LocalLifecycleRuntime:
        return LocalLifecycleRuntime(self.manifest_root, self.manifest)

    def plan(self) -> LifecyclePlanV1:
        return plan_lifecycle(self.manifest, self.snapshots, self.runtime().observe())


class LocalLifecycleRuntime:
    """Technical lifecycle mutations composed exclusively from existing services."""

    def __init__(self, manifest_root: Path, manifest: LifecycleManifestV1) -> None:
        self._manifest_root = manifest_root
        self._repository_path = manifest.repository.path
        self._config = desired_repository_config(manifest)

    @property
    def repository_root(self) -> Path:
        return self._manifest_root.joinpath(*Path(self._repository_path).parts)

    def observe(self) -> RepositoryObservation:
        target = resolve_repository_target(self._manifest_root, self._repository_path)
        inspection = inspect_repository_target(target, self._config)
        if inspection.code is RepositoryTargetInspectionCode.ABSENT:
            return RepositoryObservation(RepositoryObservationState.ABSENT)
        if inspection.code is RepositoryTargetInspectionCode.CONFLICT:
            return RepositoryObservation(RepositoryObservationState.CONFLICT, self._config)
        observation = inspection.observation
        if observation is None:
            raise RuntimeError("compatible repository inspection has no read capability")
        try:
            with observation:
                return observe_local_repository(observation, self._config)
        except SourceContentError as error:
            raise RuntimeError("canonical source observation failed") from error

    def initialize(self, config: LocalRepositoryConfig) -> None:
        if config != self._config:
            raise ValueError("lifecycle repository configuration changed")
        try:
            target = resolve_repository_target(self._manifest_root, self._repository_path)
            initialize_repository_target(target, config)
        except RepositoryTargetError as error:
            raise LifecycleRuntimeConflictError(
                "repository target changed during initialization"
            ) from error

    def create_course(
        self,
        desired_course: DesiredCourse,
        context: ExecutionContext,
        expected_high_water: int,
    ) -> None:
        try:
            with self._mutation_repository() as repository:
                repository.course_service.create(
                    desired_course_profile(desired_course),
                    context,
                    expected_sequence=expected_high_water,
                )
        except (CourseConflictError, RetryableCourseConflictError) as error:
            raise LifecycleRuntimeConflictError(
                "course stream changed during lifecycle apply"
            ) from error

    def ingest_source(
        self,
        desired_source: DesiredSource,
        snapshot: SourceSnapshot,
        context: ExecutionContext,
        expected_high_water: int,
    ) -> None:
        try:
            with self._mutation_repository() as repository:
                repository.for_course(context.course_id).ingestion.ingest(
                    filename=snapshot.filename,
                    content=snapshot.content,
                    source_id=SourceId(desired_source.source_id),
                    title=desired_source_title(desired_source),
                    trust_level=desired_source.trust_level,
                    source_role=desired_source.source_role,
                    context=context,
                    expected_sequence=expected_high_water,
                )
        except TextIngestionError as error:
            if error.code is IngestionErrorCode.SEQUENCE_CONFLICT and error.retryable:
                raise LifecycleRuntimeConflictError(
                    "course stream changed during lifecycle ingestion"
                ) from error
            raise RuntimeError("lifecycle source mutation failed") from error

    def rebuild_index(self) -> None:
        try:
            with self._mutation_repository() as repository:
                repository.rebuild_retrieval()
        except LifecycleRuntimeConflictError:
            raise
        except (OSError, RuntimeError, ValueError, LocalRepositoryError) as error:
            raise LifecycleRuntimeIndexError("discardable retrieval rebuild failed") from error

    @contextmanager
    def _mutation_repository(self) -> Iterator[LocalRepository]:
        """Retain one freshly inspected owner throughout a canonical mutation."""
        try:
            target = resolve_repository_target(self._manifest_root, self._repository_path)
            inspection = inspect_repository_target(target, self._config)
            if inspection.code is not RepositoryTargetInspectionCode.COMPATIBLE:
                raise RepositoryTargetError("repository target is no longer compatible")
            observation = inspection.observation
            if observation is None:
                raise RepositoryTargetError(
                    "compatible repository inspection has no mutation capability"
                )
            with (
                observation,
                observation.mutation_scope(),
                LocalRepository.from_observation(observation, self._config) as repository,
            ):
                yield repository
        except (RepositoryTargetError, SQLiteConnectionIdentityError) as error:
            raise LifecycleRuntimeConflictError(
                "repository target changed during lifecycle mutation"
            ) from error
        except sqlite3.Error as error:
            raise RuntimeError("lifecycle SQLite mutation failed") from error


__all__ = [
    "LifecyclePlanExpectationError",
    "LocalLifecycleInputs",
    "LocalLifecycleRuntime",
]
