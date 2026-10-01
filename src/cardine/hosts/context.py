"""Single-read composition of the redacted tutor-host decision context."""

from __future__ import annotations

from collections.abc import Mapping
from typing import TYPE_CHECKING, Protocol, cast

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


_MAX_RECENT_CONVERSATION_ENTRIES = 24


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


class PrivateTutorNoteView(Protocol):
    def validated_memory_ids(
        self, course_id: CourseId, *, through_sequence: int | None = None
    ) -> frozenset[str]: ...


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
        private_notes: PrivateTutorNoteView | None = None,
    ) -> None:
        self._snapshots = snapshots
        self._evidence = evidence
        self._capabilities = capabilities
        self._presentations = presentations
        self._tools = tools
        self._private_notes = private_notes

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
        private_note_ids = (
            frozenset()
            if self._private_notes is None
            else self._private_notes.validated_memory_ids(
                course_id, through_sequence=snapshot.high_water_sequence
            )
        )
        tutor_snapshot = _without_study_memory(
            snapshot.to_json(), private_note_ids
        )
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
        presentations: tuple[JsonObject, ...] = ()
        if self._presentations is not None:
            presentations = tuple(
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
            )
        tutor_snapshot = _bounded_decision_history(tutor_snapshot, presentations)
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


def _bounded_decision_history(
    tutor_snapshot: JsonObject, presentations: tuple[JsonObject, ...]
) -> JsonObject:
    timeline_value = tutor_snapshot.get("timeline", ())
    timeline = (
        tuple(item for item in timeline_value if isinstance(item, Mapping))
        if isinstance(timeline_value, tuple)
        else ()
    )
    sequences = sorted(
        sequence
        for item in (*timeline, *presentations)
        if type(sequence := item.get("course_sequence")) is int
    )
    retained = frozenset(sequences[-_MAX_RECENT_CONVERSATION_ENTRIES:])
    conversation_sequences = frozenset(
        sequence
        for item in timeline
        if item.get("kind") in {"learner", "assistant"}
        and type(sequence := item.get("course_sequence")) is int
    ) | frozenset(
        sequence
        for item in presentations
        if item.get("kind") in {"assistant_message", "learner_question"}
        and type(sequence := item.get("course_sequence")) is int
    )
    included_conversation = len(conversation_sequences & retained)
    return {
        **tutor_snapshot,
        "timeline": tuple(
            item for item in timeline if item.get("course_sequence") in retained
        ),
        "tutor_presentations": tuple(
            item for item in presentations if item.get("course_sequence") in retained
        ),
        "conversation_window": {
            "total_entries": len(conversation_sequences),
            "included_entries": included_conversation,
            "omitted_entries": len(conversation_sequences) - included_conversation,
            "through_sequence": tutor_snapshot.get("high_water_sequence", 0),
        },
    }


def _without_study_memory(
    snapshot: JsonObject, private_note_ids: frozenset[str]
) -> JsonObject:
    """Keep structured memory out of ordinary provider conversation context."""

    timeline = snapshot.get("timeline", ())
    notes = snapshot.get("notes", ())
    session = snapshot.get("session")
    filtered: dict[str, object] = dict(snapshot)
    private_contents = frozenset(
        content
        for item in (
            timeline if isinstance(timeline, tuple) else ()
        )
        if isinstance(item, Mapping)
        and str(item.get("interaction_id", "")) in private_note_ids
        and isinstance((content := item.get("content")), str)
    )
    if isinstance(timeline, tuple):
        filtered["timeline"] = tuple(
            item
            for item in timeline
            if not (
                isinstance(item, Mapping)
                and str(item.get("interaction_id", "")) in private_note_ids
            )
        )
    if isinstance(notes, tuple):
        filtered["notes"] = tuple(
            item
            for item in notes
            if not (
                isinstance(item, Mapping)
                and str(item.get("interaction_id", "")) in private_note_ids
            )
        )
    if isinstance(session, Mapping):
        summary = session.get("continuation_summary")
        if isinstance(summary, Mapping):
            cleaned_summary = dict(summary)
            for key in ("grounded_points", "unresolved_notes"):
                values = summary.get(key)
                if isinstance(values, tuple):
                    cleaned_summary[key] = tuple(
                        value for value in values if value not in private_contents
                    )
            cleaned_session = dict(session)
            cleaned_session["continuation_summary"] = cleaned_summary
            filtered["session"] = cleaned_session
    return cast(JsonObject, filtered)


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
