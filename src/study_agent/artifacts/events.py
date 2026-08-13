"""Strict v1 codecs for canonical artifact proposal and decision events."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from hashlib import sha256

from study_agent.domain import (
    Actor,
    ArtifactBatchId,
    ArtifactDecision,
    ArtifactId,
    ArtifactRevisionId,
    CorrelationId,
    CourseId,
    DomainEvent,
    EventId,
    PrincipalKind,
    RunId,
    SessionId,
    StudyArtifactKind,
    artifact_event_id_for,
)
from study_agent.domain._validation import JsonObject, JsonValue
from study_agent.state import canonical_json_bytes

from .content import StudyArtifactEnvelope
from .contracts import (
    ArtifactDecisionRequest,
    ArtifactProposalOrigin,
    GeneratedBatchProofReceipt,
    ServiceDecisionPolicyReceipt,
)
from .identity import (
    ArtifactProvenanceOrigin,
    HumanAuthoredArtifactProvenance,
    artifact_batch_id_for,
    artifact_id_for,
    artifact_provenance_from_bytes,
    artifact_revision_id_for,
    human_authored_artifact_batch_id_for,
)

PROPOSAL_BATCH_RECORDED = "study_artifact.proposal_batch_recorded"
DECISION_RECORDED = "study_artifact.decision_recorded"
ARTIFACT_SCHEMA_VERSION = 1
ARTIFACT_EVENT_TYPES = frozenset({PROPOSAL_BATCH_RECORDED, DECISION_RECORDED})


@dataclass(frozen=True, slots=True)
class RecordedArtifactProposal:
    ordinal: int
    artifact_id: ArtifactId
    revision_id: ArtifactRevisionId
    kind: StudyArtifactKind
    content_bytes: bytes
    provenance_bytes: bytes
    prior_revision_id: ArtifactRevisionId | None
    parent_artifact_id: ArtifactId | None


@dataclass(frozen=True, slots=True)
class ProposalBatchRecorded:
    batch_id: ArtifactBatchId
    origin: ArtifactProposalOrigin
    proposals: tuple[RecordedArtifactProposal, ...]
    run_id: RunId | None
    proof: GeneratedBatchProofReceipt | None
    idempotency_key: str
    command_fingerprint: str


@dataclass(frozen=True, slots=True)
class DecisionRecorded:
    revision_id: ArtifactRevisionId
    decision: ArtifactDecision
    supersedes_revision_id: ArtifactRevisionId | None
    policy_receipt: ServiceDecisionPolicyReceipt | None
    idempotency_key: str
    command_fingerprint: str


def proposal_batch_payload(
    batch_id: ArtifactBatchId,
    origin: ArtifactProposalOrigin,
    proposals: tuple[RecordedArtifactProposal, ...],
    session_id: SessionId,
    idempotency_key: str,
    *,
    run_id: RunId | None,
    proof: GeneratedBatchProofReceipt | None,
) -> JsonObject:
    manifest = tuple(_proposal_manifest(item) for item in proposals)
    fingerprint = proposal_command_fingerprint(origin, run_id, manifest)
    return {
        "batch_id": str(batch_id),
        "session_id": str(session_id),
        "origin": origin.value,
        "proposals": manifest,
        "run_id": str(run_id) if run_id else None,
        "proof": _proof_manifest(proof) if proof else None,
        "idempotency_key": idempotency_key,
        "command_fingerprint": fingerprint,
    }


def decision_payload(
    revision_id: ArtifactRevisionId,
    decision: ArtifactDecision,
    supersedes_revision_id: ArtifactRevisionId | None,
    session_id: SessionId,
    idempotency_key: str,
    policy_receipt: ServiceDecisionPolicyReceipt | None,
) -> JsonObject:
    fingerprint = (
        service_decision_command_fingerprint(revision_id)
        if policy_receipt is not None
        else decision_command_fingerprint(revision_id, decision, supersedes_revision_id, None)
    )
    return {
        "revision_id": str(revision_id),
        "session_id": str(session_id),
        "decision": decision.value,
        "supersedes_revision_id": (str(supersedes_revision_id) if supersedes_revision_id else None),
        "policy_receipt": _policy_manifest(policy_receipt) if policy_receipt else None,
        "idempotency_key": idempotency_key,
        "command_fingerprint": fingerprint,
    }


def decode_proposal_batch_recorded(event: DomainEvent) -> ProposalBatchRecorded:
    payload = _envelope(event, PROPOSAL_BATCH_RECORDED, "proposal")
    if not isinstance(event.session_id, SessionId):  # narrowed after envelope validation
        raise ValueError("artifact proposal event requires typed session identity")
    _exact(
        payload,
        {
            "batch_id",
            "session_id",
            "origin",
            "proposals",
            "run_id",
            "proof",
            "idempotency_key",
            "command_fingerprint",
        },
        "proposal batch",
    )
    origin = ArtifactProposalOrigin(_text(payload.get("origin"), "origin"))
    proposals = tuple(_decode_proposal(item) for item in _objects(payload, "proposals"))
    run_raw = payload.get("run_id")
    run_id = RunId(run_raw) if isinstance(run_raw, str) and run_raw else None
    proof_raw = payload.get("proof")
    proof = _decode_proof(proof_raw) if isinstance(proof_raw, Mapping) else None
    if origin is ArtifactProposalOrigin.GENERATED:
        if event.actor.kind is not PrincipalKind.SERVICE or run_id is None or proof is None:
            raise ValueError("generated proposal event requires SERVICE authority and proof")
        if any(
            artifact_provenance_from_bytes(item.provenance_bytes).origin
            is not ArtifactProvenanceOrigin.GENERATED
            for item in proposals
        ):
            raise ValueError("generated proposal batch has mixed provenance origin")
    else:
        if event.actor.kind is not PrincipalKind.HUMAN or run_id is not None or proof is not None:
            raise ValueError("human proposal event requires HUMAN authority without run proof")
        if len(proposals) != 1:
            raise ValueError("human-authored batch must contain exactly one proposal")
        provenance = artifact_provenance_from_bytes(proposals[0].provenance_bytes)
        if provenance.origin is not ArtifactProvenanceOrigin.HUMAN_AUTHORED:
            raise ValueError("human proposal event requires human-authored provenance")
    _validate_proposals(proposals)
    if origin is ArtifactProposalOrigin.GENERATED:
        assert run_id is not None
        expected_batch = artifact_batch_id_for(
            event.course_id, event.session_id, run_id, _key(payload)
        )
    else:
        human_provenance = artifact_provenance_from_bytes(proposals[0].provenance_bytes)
        if not isinstance(human_provenance, HumanAuthoredArtifactProvenance):
            raise ValueError("human proposal provenance is invalid")
        expected_batch = human_authored_artifact_batch_id_for(
            event.course_id,
            event.session_id,
            human_provenance.interaction_id,
            _key(payload),
        )
    if ArtifactBatchId(_text(payload.get("batch_id"), "batch_id")) != expected_batch:
        raise ValueError("artifact batch id does not match command identity")
    for proposal in proposals:
        provenance = artifact_provenance_from_bytes(proposal.provenance_bytes)
        if proposal.prior_revision_id is None and proposal.artifact_id != artifact_id_for(
            expected_batch, proposal.ordinal
        ):
            raise ValueError("new artifact id does not match batch ordinal")
        expected_revision = artifact_revision_id_for(
            proposal.artifact_id,
            proposal.kind,
            proposal.content_bytes,
            proposal.provenance_bytes,
            proposal.prior_revision_id,
        )
        if proposal.revision_id != expected_revision:
            raise ValueError("artifact revision id does not match canonical identity")
        if provenance.prior_revision_id != proposal.prior_revision_id:
            raise ValueError("proposal prior revision does not match provenance")
    fingerprint = _fingerprint(payload)
    manifest = tuple(_proposal_manifest(item) for item in proposals)
    if fingerprint != proposal_command_fingerprint(origin, run_id, manifest):
        raise ValueError("proposal command fingerprint mismatch")
    return ProposalBatchRecorded(
        ArtifactBatchId(_text(payload.get("batch_id"), "batch_id")),
        origin,
        proposals,
        run_id,
        proof,
        _key(payload),
        fingerprint,
    )


def decode_decision_recorded(event: DomainEvent) -> DecisionRecorded:
    payload = _envelope(event, DECISION_RECORDED, "decision")
    _exact(
        payload,
        {
            "revision_id",
            "session_id",
            "decision",
            "supersedes_revision_id",
            "policy_receipt",
            "idempotency_key",
            "command_fingerprint",
        },
        "artifact decision",
    )
    revision_id = ArtifactRevisionId(_text(payload.get("revision_id"), "revision_id"))
    decision = ArtifactDecision(_text(payload.get("decision"), "decision"))
    supersedes_raw = payload.get("supersedes_revision_id")
    supersedes = ArtifactRevisionId(supersedes_raw) if isinstance(supersedes_raw, str) else None
    receipt_raw = payload.get("policy_receipt")
    receipt = _decode_policy(receipt_raw) if isinstance(receipt_raw, Mapping) else None
    if event.actor.kind is PrincipalKind.HUMAN:
        if receipt is not None:
            raise ValueError("human decision cannot carry service policy receipt")
    elif event.actor.kind is PrincipalKind.SERVICE:
        if receipt is None:
            raise ValueError("service decision requires policy receipt")
        if (
            receipt.request_id != str(event.event_id)
            or receipt.decision is not decision
            or receipt.supersedes_revision_id != supersedes
        ):
            raise ValueError("service policy receipt does not bind the event outcome")
    else:
        raise ValueError("MODEL cannot decide artifacts")
    if decision is ArtifactDecision.REJECT and supersedes is not None:
        raise ValueError("reject never supersedes")
    fingerprint = _fingerprint(payload)
    expected_fingerprint = (
        service_decision_command_fingerprint(revision_id)
        if receipt is not None
        else decision_command_fingerprint(revision_id, decision, supersedes, None)
    )
    if fingerprint != expected_fingerprint:
        raise ValueError("decision command fingerprint mismatch")
    return DecisionRecorded(revision_id, decision, supersedes, receipt, _key(payload), fingerprint)


def proposal_command_fingerprint(
    origin: ArtifactProposalOrigin,
    run_id: RunId | None,
    proposals: tuple[JsonValue, ...],
) -> str:
    # A verified run identity is the complete public generated command. Its
    # recovered output is trusted proof, not caller input, so exact retries can
    # resolve without invoking the proof port again.
    portable_proposals: tuple[JsonValue, ...] = tuple(
        {
            "ordinal": item.get("ordinal"),
            "artifact_id": item.get("artifact_id"),
            "kind": item.get("kind"),
            "content": item.get("content"),
            "provenance": item.get("provenance"),
        }
        for item in proposals
        if isinstance(item, Mapping)
    )
    return _sha(
        {
            "origin": origin.value,
            "run_id": str(run_id) if run_id else None,
            "proposals": () if origin is ArtifactProposalOrigin.GENERATED else portable_proposals,
        }
    )


def decision_command_fingerprint(
    revision_id: ArtifactRevisionId,
    decision: ArtifactDecision,
    supersedes_revision_id: ArtifactRevisionId | None,
    policy_receipt: ServiceDecisionPolicyReceipt | None,
) -> str:
    return _sha(
        {
            "revision_id": str(revision_id),
            "decision": decision.value,
            "supersedes_revision_id": str(supersedes_revision_id)
            if supersedes_revision_id
            else None,
            # Policy is invoked only after this stable request identity is
            # checked. Its signed result is validated and persisted separately.
            "policy_receipt": None,
        }
    )


def service_decision_command_fingerprint(revision_id: ArtifactRevisionId) -> str:
    return _sha({"revision_id": str(revision_id), "authority": "service_policy"})


def bulk_manifest_fingerprint(decisions: tuple[ArtifactDecisionRequest, ...]) -> str:
    """Hash the ordered decision manifest without including its retry key."""
    return _sha(
        {
            "decisions": tuple(
                {
                    "ordinal": ordinal,
                    "revision_id": str(item.revision_id),
                    "decision": item.decision.value,
                    "supersedes_revision_id": (
                        str(item.supersedes_revision_id)
                        if item.supersedes_revision_id is not None
                        else None
                    ),
                }
                for ordinal, item in enumerate(decisions)
            )
        }
    )


def bulk_request_fingerprint(
    course_id: CourseId,
    session_id: SessionId,
    bulk_key: str,
    manifest_fingerprint: str,
) -> str:
    """Bind a bulk manifest to its course/session and host retry key."""
    return _sha(
        {
            "course_id": str(course_id),
            "session_id": str(session_id),
            "bulk_key": bulk_key,
            "manifest_fingerprint": manifest_fingerprint,
        }
    )


def bulk_item_idempotency_key(
    bulk_key: str, ordinal: int, manifest_fingerprint: str
) -> str:
    """Derive one opaque child identity from bulk key, ordinal, and manifest."""
    key_digest = sha256(bulk_key.encode("utf-8")).hexdigest()
    return f"artifact-bulk@1:{key_digest}:{ordinal}:{manifest_fingerprint}"


def bulk_item_prefix(bulk_key: str) -> str:
    """Return the scan prefix used to detect changed bulk-manifest retries."""
    key_digest = sha256(bulk_key.encode("utf-8")).hexdigest()
    return f"artifact-bulk@1:{key_digest}:"


def _proposal_manifest(value: RecordedArtifactProposal) -> JsonObject:
    return {
        "ordinal": value.ordinal,
        "artifact_id": str(value.artifact_id),
        "revision_id": str(value.revision_id),
        "kind": value.kind.value,
        "content": value.content_bytes.decode("utf-8"),
        "provenance": value.provenance_bytes.decode("utf-8"),
        "prior_revision_id": str(value.prior_revision_id) if value.prior_revision_id else None,
        "parent_artifact_id": str(value.parent_artifact_id) if value.parent_artifact_id else None,
    }


def _decode_proposal(value: Mapping[str, JsonValue]) -> RecordedArtifactProposal:
    _exact(
        value,
        {
            "ordinal",
            "artifact_id",
            "revision_id",
            "kind",
            "content",
            "provenance",
            "prior_revision_id",
            "parent_artifact_id",
        },
        "artifact proposal",
    )
    prior = value.get("prior_revision_id")
    parent = value.get("parent_artifact_id")
    content = _text(value.get("content"), "content").encode()
    provenance = _text(value.get("provenance"), "provenance").encode()
    StudyArtifactEnvelope.from_bytes(content)
    artifact_provenance_from_bytes(provenance)
    return RecordedArtifactProposal(
        _integer(value.get("ordinal"), "ordinal"),
        ArtifactId(_text(value.get("artifact_id"), "artifact_id")),
        ArtifactRevisionId(_text(value.get("revision_id"), "revision_id")),
        StudyArtifactKind(_text(value.get("kind"), "kind")),
        content,
        provenance,
        ArtifactRevisionId(prior) if isinstance(prior, str) else None,
        ArtifactId(parent) if isinstance(parent, str) else None,
    )


def _validate_proposals(proposals: tuple[RecordedArtifactProposal, ...]) -> None:
    if not 1 <= len(proposals) <= 24:
        raise ValueError("proposal batch must contain 1..24 proposals")
    if tuple(item.ordinal for item in proposals) != tuple(range(len(proposals))):
        raise ValueError("proposal ordinals must be contiguous from zero")
    if len({item.artifact_id for item in proposals}) != len(proposals):
        raise ValueError("proposal batch may contain each artifact only once")


def _envelope(event: DomainEvent, event_type: str, command_kind: str) -> JsonObject:
    if event.event_type != event_type or event.schema_version != ARTIFACT_SCHEMA_VERSION:
        raise ValueError(f"event envelope does not match {event_type}@1")
    if event.session_id is None or not isinstance(event.session_id, SessionId):
        raise ValueError("artifact events must be session-scoped")
    if not isinstance(event.course_id, CourseId) or event.course_sequence < 2:
        raise ValueError("artifact event course envelope is invalid")
    if not isinstance(event.event_id, EventId) or not isinstance(
        event.correlation_id, CorrelationId
    ):
        raise ValueError("artifact event identity envelope is invalid")
    if not isinstance(event.actor, Actor) or not isinstance(event.actor.kind, PrincipalKind):
        raise ValueError("artifact event actor envelope is invalid")
    if event.payload.get("session_id") != str(event.session_id):
        raise ValueError("payload session id must match event envelope")
    expected = artifact_event_id_for(
        event.course_id, event.session_id, _key(event.payload), command_kind
    )
    if event.event_id != expected:
        raise ValueError("artifact event id does not match command identity")
    return event.payload


def _proof_manifest(value: GeneratedBatchProofReceipt) -> JsonObject:
    return {
        "verifier_id": value.verifier_id,
        "verifier_version": value.verifier_version,
        "verifier_fingerprint": value.verifier_fingerprint,
    }


def _decode_proof(value: Mapping[str, JsonValue]) -> GeneratedBatchProofReceipt:
    _exact(
        value, {"verifier_id", "verifier_version", "verifier_fingerprint"}, "generated batch proof"
    )
    return GeneratedBatchProofReceipt(
        _text(value.get("verifier_id"), "verifier_id"),
        _text(value.get("verifier_version"), "verifier_version"),
        _text(value.get("verifier_fingerprint"), "verifier_fingerprint"),
    )


def _policy_manifest(value: ServiceDecisionPolicyReceipt) -> JsonObject:
    return {
        "request_id": value.request_id,
        "decision": value.decision.value,
        "supersedes_revision_id": str(value.supersedes_revision_id)
        if value.supersedes_revision_id
        else None,
        "policy_id": value.policy_id,
        "policy_version": value.policy_version,
        "policy_fingerprint": value.policy_fingerprint,
        "result_fingerprint": value.result_fingerprint,
    }


def _decode_policy(value: Mapping[str, JsonValue]) -> ServiceDecisionPolicyReceipt:
    _exact(
        value,
        {
            "request_id",
            "decision",
            "supersedes_revision_id",
            "policy_id",
            "policy_version",
            "policy_fingerprint",
            "result_fingerprint",
        },
        "policy receipt",
    )
    supersedes = value.get("supersedes_revision_id")
    return ServiceDecisionPolicyReceipt(
        _text(value.get("request_id"), "request_id"),
        ArtifactDecision(_text(value.get("decision"), "decision")),
        ArtifactRevisionId(supersedes) if isinstance(supersedes, str) else None,
        _text(value.get("policy_id"), "policy_id"),
        _text(value.get("policy_version"), "policy_version"),
        _text(value.get("policy_fingerprint"), "policy_fingerprint"),
        _text(value.get("result_fingerprint"), "result_fingerprint"),
    )


def _objects(value: Mapping[str, JsonValue], key: str) -> tuple[Mapping[str, JsonValue], ...]:
    raw = value.get(key)
    if not isinstance(raw, (tuple, list)) or any(not isinstance(item, Mapping) for item in raw):
        raise ValueError(f"{key} must be an array of objects")
    return tuple(item for item in raw if isinstance(item, Mapping))


def _exact(value: Mapping[str, JsonValue], fields: set[str], name: str) -> None:
    if set(value) != fields:
        raise ValueError(f"{name} fields mismatch")


def _text(value: JsonValue | None, name: str) -> str:
    if not isinstance(value, str) or not value or value != value.strip():
        raise ValueError(f"{name} must be non-empty canonical text")
    return value


def _integer(value: JsonValue | None, name: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{name} must be an integer")
    return value


def _key(payload: Mapping[str, JsonValue]) -> str:
    return _text(payload.get("idempotency_key"), "idempotency_key")


def _fingerprint(payload: Mapping[str, JsonValue]) -> str:
    value = _text(payload.get("command_fingerprint"), "command_fingerprint")
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("command_fingerprint must be a lowercase SHA-256 digest")
    return value


def _sha(value: JsonObject) -> str:
    return sha256(canonical_json_bytes(value)).hexdigest()


__all__ = [
    "ARTIFACT_EVENT_TYPES",
    "ARTIFACT_SCHEMA_VERSION",
    "DECISION_RECORDED",
    "PROPOSAL_BATCH_RECORDED",
    "DecisionRecorded",
    "ProposalBatchRecorded",
    "RecordedArtifactProposal",
    "bulk_item_idempotency_key",
    "bulk_item_prefix",
    "bulk_manifest_fingerprint",
    "bulk_request_fingerprint",
    "decision_command_fingerprint",
    "decision_payload",
    "decode_decision_recorded",
    "decode_proposal_batch_recorded",
    "proposal_batch_payload",
    "proposal_command_fingerprint",
    "service_decision_command_fingerprint",
]
