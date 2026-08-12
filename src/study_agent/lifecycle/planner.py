"""Pure, deterministic desired-versus-observed lifecycle planning."""

from __future__ import annotations

from datetime import date
from hashlib import sha256

from cardine.domain.course import CourseProfile
from study_agent.domain._validation import JsonObject
from study_agent.domain.identifiers import CourseId
from study_agent.domain.source import SourceKind
from study_agent.ports.source_input import SourceSnapshot
from study_agent.repository_config import LocalRepositoryConfig
from study_agent.state import canonical_json_bytes

from .contracts import (
    DesiredCourse,
    DesiredSource,
    IndexObservationState,
    LifecycleActionKind,
    LifecycleActionOwner,
    LifecycleActionV1,
    LifecycleCourseHighWater,
    LifecycleManifestV1,
    LifecyclePlanV1,
    LifecycleSourceChecksum,
    LifecycleStatusKind,
    LifecycleStatusV1,
    ObservedSource,
    RepositoryObservation,
    RepositoryObservationState,
)

_COURSE_FINGERPRINT_DOMAIN = b"study-agent-lifecycle-course-v1\0"
_SOURCE_FINGERPRINT_DOMAIN = b"study-agent-lifecycle-source-v1\0"


def plan_lifecycle(
    manifest: LifecycleManifestV1,
    snapshots: tuple[SourceSnapshot, ...],
    observed: RepositoryObservation,
) -> LifecyclePlanV1:
    """Return a declarative plan without reading or mutating external state."""

    if not isinstance(manifest, LifecycleManifestV1):
        raise TypeError("manifest must be a LifecycleManifestV1")
    if not isinstance(snapshots, tuple) or any(
        not isinstance(snapshot, SourceSnapshot) for snapshot in snapshots
    ):
        raise TypeError("snapshots must be a tuple of SourceSnapshot values")
    if not isinstance(observed, RepositoryObservation):
        raise TypeError("observed must be a RepositoryObservation")

    desired_sources = tuple(
        (course, source) for course in manifest.courses for source in course.sources
    )
    if len(snapshots) != len(desired_sources):
        raise ValueError("snapshots must correspond 1:1 with manifest sources")
    for (_, source), snapshot in zip(desired_sources, snapshots, strict=True):
        if snapshot.relative_path != source.path:
            raise ValueError("snapshot order and paths must match manifest sources exactly")

    checksums = tuple(
        LifecycleSourceChecksum(
            course.course_id,
            source.source_id,
            source.path,
            snapshot.checksum_sha256,
            snapshot.byte_size,
        )
        for (course, source), snapshot in zip(desired_sources, snapshots, strict=True)
    )
    high_waters = tuple(
        LifecycleCourseHighWater(course.course_id, course.high_water_sequence)
        for course in observed.courses
    )
    actions: list[LifecycleActionV1] = []

    def action(
        kind: LifecycleActionKind,
        owner: LifecycleActionOwner,
        code: str,
        *,
        course_id: str | None = None,
        source_id: str | None = None,
        expected_high_water: int | None = None,
        desired_fingerprint: str | None = None,
    ) -> None:
        actions.append(
            LifecycleActionV1(
                len(actions),
                kind,
                owner,
                code,
                course_id,
                source_id,
                expected_high_water,
                desired_fingerprint,
            )
        )

    if observed.state is RepositoryObservationState.ABSENT:
        action(
            LifecycleActionKind.INITIALIZE,
            LifecycleActionOwner.REPOSITORY,
            "repository_absent",
            desired_fingerprint=_config_fingerprint(manifest),
        )
        return LifecyclePlanV1(manifest.fingerprint, checksums, high_waters, tuple(actions))

    expected_config = desired_repository_config(manifest)
    if observed.state is RepositoryObservationState.CONFLICT or observed.config != expected_config:
        action(
            LifecycleActionKind.CONFLICT,
            LifecycleActionOwner.REPOSITORY,
            "repository_config_conflict",
            desired_fingerprint=_config_fingerprint(manifest),
        )
        return LifecyclePlanV1(manifest.fingerprint, checksums, high_waters, tuple(actions))

    action(
        LifecycleActionKind.NOOP,
        LifecycleActionOwner.REPOSITORY,
        "repository_compatible",
        desired_fingerprint=_config_fingerprint(manifest),
    )

    desired_course_by_id = {course.course_id: course for course in manifest.courses}
    observed_course_by_id = {course.course_id: course for course in observed.courses}
    mutable_high_waters: dict[str, int] = {
        course.course_id: course.high_water_sequence for course in observed.courses
    }
    blocked_courses: set[str] = set()

    # Course ownership is decided before any source action.
    for course_id in sorted(set(desired_course_by_id) | set(observed_course_by_id)):
        desired_course = desired_course_by_id.get(course_id)
        current_course = observed_course_by_id.get(course_id)
        if desired_course is None:
            action(
                LifecycleActionKind.WARNING,
                LifecycleActionOwner.COURSE,
                "course_removed_from_manifest_ignored",
                course_id=course_id,
                expected_high_water=(
                    current_course.high_water_sequence if current_course else None
                ),
            )
            continue
        desired_profile = desired_course_profile(desired_course)
        fingerprint = desired_course_fingerprint(desired_course)
        if current_course is None:
            action(
                LifecycleActionKind.CREATE_COURSE,
                LifecycleActionOwner.COURSE,
                "course_absent",
                course_id=course_id,
                expected_high_water=0,
                desired_fingerprint=fingerprint,
            )
            mutable_high_waters[course_id] = 1
        elif current_course.profile != desired_profile:
            action(
                LifecycleActionKind.CONFLICT,
                LifecycleActionOwner.COURSE,
                "course_profile_conflict",
                course_id=course_id,
                expected_high_water=current_course.high_water_sequence,
                desired_fingerprint=fingerprint,
            )
            blocked_courses.add(course_id)
        else:
            action(
                LifecycleActionKind.NOOP,
                LifecycleActionOwner.COURSE,
                "course_converged",
                course_id=course_id,
                expected_high_water=current_course.high_water_sequence,
                desired_fingerprint=fingerprint,
            )

    snapshot_by_identity = {
        (course.course_id, source.source_id): snapshot
        for (course, source), snapshot in zip(desired_sources, snapshots, strict=True)
    }
    # Sources are globally ordered by (course_id, source_id).
    for course in manifest.courses:
        if course.course_id in blocked_courses:
            continue
        current_course = observed_course_by_id.get(course.course_id)
        observed_sources = (
            {source.source_id: source for source in current_course.sources}
            if current_course is not None
            else {}
        )
        desired_source_by_id = {source.source_id: source for source in course.sources}
        for source_id in sorted(set(desired_source_by_id) | set(observed_sources)):
            desired_source = desired_source_by_id.get(source_id)
            current_source = observed_sources.get(source_id)
            expected_sequence = mutable_high_waters[course.course_id]
            if desired_source is None:
                action(
                    LifecycleActionKind.WARNING,
                    LifecycleActionOwner.SOURCE,
                    "source_removed_from_manifest_ignored",
                    course_id=course.course_id,
                    source_id=source_id,
                    expected_high_water=expected_sequence,
                )
                continue
            snapshot = snapshot_by_identity[(course.course_id, source_id)]
            fingerprint = desired_source_fingerprint(desired_source, snapshot)
            if current_source is None:
                code = "source_absent"
            elif not _source_matches(desired_source, snapshot, current_source):
                code = "source_changed"
            else:
                action(
                    LifecycleActionKind.NOOP,
                    LifecycleActionOwner.SOURCE,
                    "source_converged",
                    course_id=course.course_id,
                    source_id=source_id,
                    expected_high_water=expected_sequence,
                    desired_fingerprint=fingerprint,
                )
                continue
            action(
                LifecycleActionKind.INGEST_REVISION,
                LifecycleActionOwner.SOURCE,
                code,
                course_id=course.course_id,
                source_id=source_id,
                expected_high_water=expected_sequence,
                desired_fingerprint=fingerprint,
            )
            mutable_high_waters[course.course_id] = expected_sequence + 1

    # Index work is discardable and therefore always ordered after canonical work.
    if not blocked_courses:
        canonical_mutation_planned = any(
            item.kind
            in {LifecycleActionKind.CREATE_COURSE, LifecycleActionKind.INGEST_REVISION}
            for item in actions
        )
        if canonical_mutation_planned:
            action(
                LifecycleActionKind.REBUILD_INDEX,
                LifecycleActionOwner.INDEX,
                "canonical_state_changed",
            )
        elif (
            observed.index is not None
            and observed.index.state is IndexObservationState.HEALTHY
        ):
            action(
                LifecycleActionKind.NOOP,
                LifecycleActionOwner.INDEX,
                "index_healthy",
            )
        else:
            code = (
                "index_missing"
                if observed.index is None or observed.index.state is IndexObservationState.MISSING
                else "index_stale"
            )
            action(
                LifecycleActionKind.REBUILD_INDEX,
                LifecycleActionOwner.INDEX,
                code,
            )

    return LifecyclePlanV1(manifest.fingerprint, checksums, high_waters, tuple(actions))


def status_for_plan(plan: LifecyclePlanV1) -> LifecycleStatusV1:
    """Classify a plan using the v1 status precedence."""

    if not isinstance(plan, LifecyclePlanV1):
        raise TypeError("plan must be a LifecyclePlanV1")
    kinds = {action.kind for action in plan.actions}
    if LifecycleActionKind.CONFLICT in kinds:
        status = LifecycleStatusKind.CANONICAL_CONFLICT
    elif any(
        action.kind in {LifecycleActionKind.INITIALIZE, LifecycleActionKind.CREATE_COURSE}
        for action in plan.actions
    ):
        status = LifecycleStatusKind.CANONICAL_DRIFT
    elif LifecycleActionKind.INGEST_REVISION in kinds:
        status = LifecycleStatusKind.SOURCE_DRIFT
    elif LifecycleActionKind.REBUILD_INDEX in kinds:
        status = LifecycleStatusKind.OPERATIONAL_DEGRADATION
    else:
        status = LifecycleStatusKind.CONVERGED
    return LifecycleStatusV1(
        status,
        plan.fingerprint,
        len(plan.actions),
        len(plan.conflicts),
        len(plan.warnings),
    )


def desired_course_profile(course: DesiredCourse) -> CourseProfile:
    """Translate manifest course intent into the canonical course contract."""

    return CourseProfile(
        CourseId(course.course_id),
        course.title,
        course.language,
        date.fromisoformat(course.exam_date) if course.exam_date is not None else None,
        course.assessment_styles,
        course.learning_goals,
    )


def _source_matches(
    desired: DesiredSource, snapshot: SourceSnapshot, observed: ObservedSource
) -> bool:
    return (
        observed.kind is desired_source_kind(desired)
        and observed.title == desired_source_title(desired)
        and observed.trust_level == desired.trust_level
        and observed.source_role == desired.source_role
        and observed.checksum_sha256 == snapshot.checksum_sha256
        and observed.byte_size == snapshot.byte_size
    )


def desired_source_kind(source: DesiredSource) -> SourceKind:
    """Return the canonical source kind implied by a validated manifest path."""

    return SourceKind.MARKDOWN if source.path.endswith(".md") else SourceKind.TEXT


def desired_source_title(source: DesiredSource) -> str:
    """Return the explicit or deterministic fallback canonical source title."""

    if source.title is not None:
        return source.title
    filename = source.path.rsplit("/", 1)[-1]
    return filename.rsplit(".", 1)[0]


def desired_course_fingerprint(course: DesiredCourse) -> str:
    """Fingerprint the immutable canonical portion of desired course state."""

    value: JsonObject = {
        "assessment_styles": course.assessment_styles,
        "course_id": course.course_id,
        "exam_date": course.exam_date,
        "language": course.language,
        "learning_goals": course.learning_goals,
        "title": course.title,
    }
    return _fingerprint(_COURSE_FINGERPRINT_DOMAIN, value)


def desired_source_fingerprint(
    source: DesiredSource, snapshot: SourceSnapshot
) -> str:
    """Fingerprint desired source metadata together with immutable input bytes."""

    value: JsonObject = {
        "byte_size": snapshot.byte_size,
        "checksum_sha256": snapshot.checksum_sha256,
        "kind": desired_source_kind(source).value,
        "source_id": source.source_id,
        "source_role": source.source_role,
        "title": desired_source_title(source),
        "trust_level": source.trust_level,
    }
    return _fingerprint(_SOURCE_FINGERPRINT_DOMAIN, value)


def desired_repository_config(manifest: LifecycleManifestV1) -> LocalRepositoryConfig:
    """Translate validated repository intent into its canonical config value."""

    return LocalRepositoryConfig(manifest.repository.model)


def _config_fingerprint(manifest: LifecycleManifestV1) -> str:
    return sha256(desired_repository_config(manifest).to_bytes()).hexdigest()


def _fingerprint(domain: bytes, value: JsonObject) -> str:
    return sha256(domain + canonical_json_bytes(value)).hexdigest()


__all__ = [
    "desired_course_fingerprint",
    "desired_course_profile",
    "desired_repository_config",
    "desired_source_fingerprint",
    "desired_source_kind",
    "desired_source_title",
    "plan_lifecycle",
    "status_for_plan",
]
