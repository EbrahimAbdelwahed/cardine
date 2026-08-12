from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, replace

import pytest

from cardine.hosts import (
    AdvertisedCapability,
    AnswerDialogueDecision,
    AskLearnerDecision,
    AssistantMessageDecision,
    HostActionIdentity,
    HostFileDescriptor,
    HostRetryReceipt,
    PendingContinuationDescriptor,
    StartCapabilityDecision,
    StopDecision,
    TutorDecision,
    TutorHostContext,
    TutorHostContextAssembler,
    TutorStopReason,
    decision_fingerprint,
    decision_from_bytes,
    decision_schema,
    decision_to_bytes,
)
from study_agent.assessments import LearnerEvidenceSnapshot
from study_agent.domain import (
    CourseId,
    SessionId,
    SessionStatus,
    StudyStatementKind,
    TutorContextField,
    TutorContextState,
    TutorSnapshotV1,
)
from study_agent.domain._validation import JsonObject

SHA_A = "a" * 64
SHA_B = "b" * 64
SHA_C = "c" * 64
STRING_INPUT: JsonObject = {
    "type": "object",
    "properties": {"topic": {"type": "string", "minLength": 1}},
    "required": ("topic",),
    "additionalProperties": False,
}
BOOLEAN_RESPONSE: JsonObject = {"type": "boolean"}


def _capability(
    capability_id: str = "grounding.ask", *, fingerprint: str = SHA_A
) -> AdvertisedCapability:
    return AdvertisedCapability(
        capability_id,
        f"{capability_id}@1.0.0",
        fingerprint,
        STRING_INPUT,
        True,
    )


def _pending() -> PendingContinuationDescriptor:
    return PendingContinuationDescriptor(
        SHA_B,
        "grounding.ask@1.0.0",
        "confirm",
        "Use this evidence?",
        BOOLEAN_RESPONSE,
    )


def _context(**changes: object) -> TutorHostContext:
    values: dict[str, object] = {
        "course_id": "course-host",
        "session_id": "session-host",
        "tutor_snapshot_sequence": 8,
        "learner_evidence_through_sequence": 8,
        "tutor_snapshot": {"status": "active"},
        "learner_evidence": {"estimates": ()},
        "advertised_capabilities": (_capability(),),
        "pending_continuation": _pending(),
        "host_files": (
            HostFileDescriptor("file-1", "lesson.pdf", "application/pdf", 12, SHA_C),
        ),
    }
    values.update(changes)
    return TutorHostContext(**values)  # type: ignore[arg-type]


def test_context_round_trip_is_canonical_and_every_binding_changes_fingerprint() -> None:
    context = _context()

    assert TutorHostContext.from_bytes(context.to_bytes()) == context
    assert context.to_bytes() == context.to_bytes()
    changed = (
        replace(context, session_id="session-other"),
        replace(context, tutor_snapshot={"status": "closed"}),
        replace(context, advertised_capabilities=(_capability(fingerprint=SHA_B),)),
        replace(context, pending_continuation=None),
        replace(context, host_files=()),
    )
    assert all(item.fingerprint != context.fingerprint for item in changed)

    noncanonical = json.dumps(
        json.loads(context.to_bytes()), separators=(",", ":")
    ).encode()
    if noncanonical == context.to_bytes():
        noncanonical += b" "
    with pytest.raises(ValueError, match="canonical"):
        TutorHostContext.from_bytes(noncanonical)


def test_decision_schema_is_closed_and_context_advertised() -> None:
    schema = decision_schema(_context())
    assert schema["type"] == "object"
    assert schema["required"] == ("decision",)
    assert schema["additionalProperties"] is False
    properties = schema["properties"]
    assert isinstance(properties, Mapping)
    decision = properties["decision"]
    assert isinstance(decision, Mapping)
    branches = decision["anyOf"]
    assert isinstance(branches, tuple)
    assert len(branches) == 1
    serialized = json.dumps(schema, sort_keys=True, default=list)
    assert "answer_dialogue" in serialized
    assert "assistant_message" not in serialized
    assert "ask_learner" not in serialized
    assert "start_capability" not in serialized
    assert "provider" not in serialized

    available = decision_schema(_context(pending_continuation=None))
    available_properties = available["properties"]
    assert isinstance(available_properties, Mapping)
    available_decision = available_properties["decision"]
    assert isinstance(available_decision, Mapping)
    available_branches = available_decision["anyOf"]
    assert isinstance(available_branches, tuple)
    assert len(available_branches) == 4
    stop_properties: Mapping[str, object] | None = None
    for branch in available_branches:
        if not isinstance(branch, Mapping):
            continue
        properties = branch.get("properties")
        if not isinstance(properties, Mapping):
            continue
        kind = properties.get("kind")
        if isinstance(kind, Mapping) and kind.get("enum") == ("stop",):
            stop_properties = properties
            break
    assert stop_properties is not None
    reason = stop_properties.get("reason")
    assert isinstance(reason, Mapping)
    assert reason.get("enum") == (
        "completed",
        "no_safe_action",
    )


def test_legacy_needs_learner_input_stop_reason_is_rejected() -> None:
    with pytest.raises(ValueError):
        TutorStopReason("needs_learner_input")


def test_context_rejects_sequence_owner_order_and_continuation_mismatches() -> None:
    with pytest.raises(ValueError, match="sequence-consistent"):
        _context(learner_evidence_through_sequence=7)
    with pytest.raises(ValueError, match="canonically ordered"):
        _context(
            advertised_capabilities=(
                _capability("z.capability", fingerprint=SHA_B),
                _capability("a.capability"),
            ),
            pending_continuation=None,
        )
    with pytest.raises(ValueError, match="not advertised"):
        _context(
            pending_continuation=replace(
                _pending(), capability_identity="other.capability@1.0.0"
            )
        )


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("tutor_snapshot", {"nested": {"api-key": "leak"}}),
        ("learner_evidence", {"canonical_expected_response": "leak"}),
    ),
)
def test_model_context_rejects_forbidden_keys(field: str, value: object) -> None:
    with pytest.raises(ValueError, match="not model-visible"):
        _context(**{field: value})


@pytest.mark.parametrize("name", ("../notes.pdf", "a/b.pdf", r"a\b.pdf"))
def test_host_file_descriptors_are_opaque_and_path_free(name: str) -> None:
    with pytest.raises(ValueError, match="path"):
        HostFileDescriptor("file-1", name, "application/pdf", 1, SHA_A)


def test_host_file_count_and_scalar_bounds_fail_closed() -> None:
    files = tuple(
        HostFileDescriptor(f"file-{index:02}", f"{index}.txt", "text/plain", 1, SHA_A)
        for index in range(17)
    )
    with pytest.raises(ValueError, match="too many"):
        _context(host_files=files)
    with pytest.raises(ValueError, match="byte_size"):
        HostFileDescriptor("file", "x.txt", "text/plain", -1, SHA_A)
    with pytest.raises(ValueError, match="bounded"):
        AskLearnerDecision("x" * 1_001)


@pytest.mark.parametrize(
    "decision",
    (
        StartCapabilityDecision("grounding.ask", {"topic": "valves"}),
        AnswerDialogueDecision(SHA_B, True),
        AskLearnerDecision("What should we review?"),
        AssistantMessageDecision("Let's begin."),
        StopDecision(TutorStopReason.COMPLETED),
    ),
)
def test_closed_decision_union_round_trips_and_has_stable_fingerprint(
    decision: TutorDecision,
) -> None:
    encoded = decision_to_bytes(decision)
    context = (
        _context()
        if isinstance(decision, AnswerDialogueDecision)
        else _context(pending_continuation=None)
    )
    recovered = decision_from_bytes(encoded, context)

    assert recovered == decision
    assert decision_fingerprint(recovered) == decision_fingerprint(decision)


def test_decisions_bind_equal_noninterned_capability_schema_and_exact_continuation() -> None:
    equal_noninterned = "".join(("grounding", ".ask"))
    assert equal_noninterned == _capability().id
    decision_from_bytes(
        decision_to_bytes(StartCapabilityDecision(equal_noninterned, {"topic": "heart"})),
        _context(pending_continuation=None),
    )
    with pytest.raises(ValueError, match="advertised schema"):
        decision_from_bytes(
            decision_to_bytes(StartCapabilityDecision(equal_noninterned, {"topic": 3})),
            _context(pending_continuation=None),
        )
    with pytest.raises(ValueError, match="exact pending"):
        decision_from_bytes(
            decision_to_bytes(AnswerDialogueDecision(SHA_C, True)),
            _context(),
        )
    with pytest.raises(ValueError, match="pending schema"):
        decision_from_bytes(
            decision_to_bytes(AnswerDialogueDecision(SHA_B, "yes")),
            _context(),
        )


@pytest.mark.parametrize(
    "forbidden_key",
    ("provider_model", "repository", "course_id", "session_id", "principal_id", "grant"),
)
def test_decision_codecs_reject_unknown_fields_and_sensitive_payloads(
    forbidden_key: str,
) -> None:
    with pytest.raises(ValueError, match="invalid field set"):
        decision_from_bytes(
            b'{"capability_id":"grounding.ask","inputs":{"topic":"x"},'
            b'"kind":"start_capability","provider":"forged"}',
            _context(),
        )
    with pytest.raises(ValueError, match=r"not model-visible|trusted-host authority"):
        StartCapabilityDecision("grounding.ask", {forbidden_key: "forged"})


def test_action_and_retry_fingerprints_bind_every_trusted_field() -> None:
    action = HostActionIdentity("action-1")
    assert action.fingerprint == HostActionIdentity("action-1").fingerprint
    assert action.fingerprint != HostActionIdentity("action-2").fingerprint

    receipt = HostRetryReceipt(
        "turn-1", action.fingerprint, _context().fingerprint, SHA_A, 1, 1
    )
    assert HostRetryReceipt.from_bytes(receipt.to_bytes()) == receipt
    assert receipt.fingerprint != replace(receipt, attempt=2).fingerprint
    assert receipt.fingerprint != replace(receipt, action_fingerprint=SHA_B).fingerprint


def test_retry_receipt_codec_has_strict_v2_migration_boundary() -> None:
    receipt = HostRetryReceipt(
        "turn-1", SHA_A, _context().fingerprint, SHA_B, 1, 1
    )
    assert json.loads(receipt.to_bytes())["schema_version"] == 2
    raw = json.loads(receipt.to_bytes())
    raw.pop("schema_version")
    legacy = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError):
        HostRetryReceipt.from_bytes(legacy)
    raw["schema_version"] = 1
    wrong_version = json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
    with pytest.raises(ValueError, match="schema version"):
        HostRetryReceipt.from_bytes(wrong_version)


@dataclass(frozen=True)
class _CapabilityId:
    value: str


@dataclass(frozen=True)
class _Manifest:
    id: _CapabilityId
    identity: str
    fingerprint: str
    input_schema: JsonObject
    supports_suspension: bool


def test_assembler_reads_snapshot_evidence_and_manifests_without_owning_state() -> None:
    course_id = CourseId("course-host")
    session_id = SessionId("session-host")
    snapshot = TutorSnapshotV1(
        course_id,
        session_id,
        8,
        SessionStatus.ACTIVE,
        None,
        (),
        tuple(
            TutorContextField(kind, TutorContextState.MISSING)
            for kind in StudyStatementKind
        ),
        (),
        (),
        (),
        (),
    )
    evidence = LearnerEvidenceSnapshot(course_id, 8, ())

    class _Snapshots:
        def get(self, requested_course: CourseId, requested_session: SessionId) -> TutorSnapshotV1:
            assert (requested_course, requested_session) == (course_id, session_id)
            return snapshot

    class _Evidence:
        def get(self, requested_course: CourseId) -> LearnerEvidenceSnapshot:
            assert requested_course == course_id
            return evidence

    class _Capabilities:
        def discover(self) -> tuple[_Manifest, ...]:
            return (
                _Manifest(
                    _CapabilityId("grounding.ask"),
                    "grounding.ask@1.0.0",
                    SHA_A,
                    STRING_INPUT,
                    True,
                ),
            )

    assembled = TutorHostContextAssembler(
        _Snapshots(), _Evidence(), _Capabilities()
    ).assemble(course_id, session_id)

    assert assembled.tutor_snapshot == snapshot.to_json()
    assert assembled.learner_evidence == {
        "course_id": "course-host",
        "through_sequence": 8,
        "estimates": (),
    }
    assert assembled.advertised_capabilities == (_capability(),)

    mismatched = LearnerEvidenceSnapshot(course_id, 7, ())

    class _StaleEvidence:
        def get(self, requested_course: CourseId) -> LearnerEvidenceSnapshot:
            assert requested_course == course_id
            return mismatched

    with pytest.raises(ValueError, match="one sequence"):
        TutorHostContextAssembler(
            _Snapshots(), _StaleEvidence(), _Capabilities()
        ).assemble(course_id, session_id)

    foreign_snapshot = replace(snapshot, course_id=CourseId("course-other"))

    class _ForeignSnapshots:
        def get(
            self, requested_course: CourseId, requested_session: SessionId
        ) -> TutorSnapshotV1:
            assert (requested_course, requested_session) == (course_id, session_id)
            return foreign_snapshot

    with pytest.raises(ValueError, match="another course or session"):
        TutorHostContextAssembler(
            _ForeignSnapshots(), _Evidence(), _Capabilities()
        ).assemble(course_id, session_id)
