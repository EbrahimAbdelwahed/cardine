"""Single-read composition of the redacted tutor-host decision context."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

from study_agent.domain import CourseId, SessionId, TutorSnapshotV1
from study_agent.domain._validation import JsonObject
from study_agent.ports.assessment import LearnerEvidenceViewPort
from study_agent.ports.session import TutorPresentationViewPort
from study_agent.ports.tutor_snapshot import TutorSnapshotPort

from .contracts import (
    AdvertisedCapability,
    HostFileDescriptor,
    PendingContinuationDescriptor,
    TutorHostContext,
)

if TYPE_CHECKING:
    from study_agent.assessments.evidence import LearnerEvidenceSnapshot


class CapabilityIdView(Protocol):
    @property
    def value(self) -> str: ...


class CapabilityManifestView(Protocol):
    @property
    def id(self) -> CapabilityIdView: ...

    @property
    def identity(self) -> str: ...

    @property
    def fingerprint(self) -> str: ...

    @property
    def input_schema(self) -> JsonObject: ...

    @property
    def supports_suspension(self) -> bool: ...


class CapabilityDiscoveryPort(Protocol):
    def discover(self) -> tuple[CapabilityManifestView, ...]: ...


class HarnessToolDiscoveryPort(Protocol):
    @property
    def manifests(self) -> tuple[HarnessToolManifestView, ...]: ...


class HarnessToolManifestView(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def input_schema(self) -> JsonObject: ...


class TutorHostContextAssembler:
    """Compose existing immutable views without becoming another state owner."""

    def __init__(
        self,
        snapshots: TutorSnapshotPort,
        evidence: LearnerEvidenceViewPort,
        capabilities: CapabilityDiscoveryPort,
        presentations: TutorPresentationViewPort | None = None,
        tools: HarnessToolDiscoveryPort | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._evidence = evidence
        self._capabilities = capabilities
        self._presentations = presentations
        self._tools = tools

    def assemble(
        self,
        course_id: CourseId,
        session_id: SessionId,
        *,
        pending_continuation: PendingContinuationDescriptor | None = None,
        host_files: tuple[HostFileDescriptor, ...] = (),
    ) -> TutorHostContext:
        snapshot = self._snapshots.get(course_id, session_id)
        evidence = self._evidence.get(course_id)
        _require_owners(snapshot, evidence, course_id, session_id)
        advertised = tuple(
            sorted(
                (
                    AdvertisedCapability(
                        item.id.value,
                        item.identity,
                        item.fingerprint,
                        item.input_schema,
                        item.supports_suspension,
                    )
                    for item in self._capabilities.discover()
                ),
                key=lambda item: (item.identity, item.manifest_fingerprint),
            )
        )
        tutor_snapshot = snapshot.to_json()
        if self._tools is not None:
            tutor_snapshot = {
                **tutor_snapshot,
                "harness_tools": tuple(
                    {
                        "name": str(item.name),
                        "input_schema": item.input_schema,
                    }
                    for item in self._tools.manifests
                ),
            }
        if self._presentations is not None:
            tutor_snapshot = {
                **tutor_snapshot,
                "tutor_presentations": tuple(
                    {
                        "kind": item.kind.value,
                        "content": item.content,
                        "course_sequence": item.course_sequence,
                        "in_reply_to_interaction_id": (
                            None
                            if item.in_reply_to_interaction_id is None
                            else str(item.in_reply_to_interaction_id)
                        ),
                    }
                    for item in self._presentations.presentations(course_id, session_id)
                ),
            }
        return TutorHostContext(
            course_id=str(course_id),
            session_id=str(session_id),
            tutor_snapshot_sequence=snapshot.high_water_sequence,
            learner_evidence_through_sequence=evidence.through_sequence,
            tutor_snapshot=tutor_snapshot,
            learner_evidence=_evidence_json(evidence),
            advertised_capabilities=advertised,
            pending_continuation=pending_continuation,
            host_files=tuple(sorted(host_files, key=lambda item: (item.id, item.checksum_sha256))),
        )


def _require_owners(
    snapshot: TutorSnapshotV1,
    evidence: LearnerEvidenceSnapshot,
    course_id: CourseId,
    session_id: SessionId,
) -> None:
    if snapshot.course_id != course_id or snapshot.session_id != session_id:
        raise ValueError("tutor snapshot belongs to another course or session")
    if evidence.course_id != course_id:
        raise ValueError("learner evidence belongs to another course")
    if snapshot.high_water_sequence != evidence.through_sequence:
        raise ValueError(
            "tutor snapshot and learner evidence were not captured through one sequence"
        )


def _evidence_json(snapshot: LearnerEvidenceSnapshot) -> JsonObject:
    return {
        "course_id": str(snapshot.course_id),
        "through_sequence": snapshot.through_sequence,
        "estimates": tuple(
            {
                "dimension": estimate.dimension.value,
                "key": estimate.key,
                "label": estimate.label,
                "numerator": estimate.numerator,
                "denominator": estimate.denominator,
                "through_sequence": estimate.through_sequence,
                "evidence": tuple(
                    {
                        "grade_id": str(reference.grade_id),
                        "event_sequence": reference.event_sequence,
                        "disposition": reference.disposition.value,
                        "numerator": reference.numerator,
                        "denominator": reference.denominator,
                    }
                    for reference in estimate.evidence
                ),
            }
            for estimate in snapshot.estimates
        ),
    }


__all__ = ["CapabilityDiscoveryPort", "TutorHostContextAssembler"]
