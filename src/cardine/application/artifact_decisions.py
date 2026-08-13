"""Cardine application contract for atomic HUMAN artifact decisions."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

from study_agent.artifacts import (
    ArtifactBulkDecisionReceipt,
    ArtifactDecisionRequest,
    ArtifactService,
)
from study_agent.domain import (
    ArtifactDecision,
    ArtifactRevisionId,
    ExecutionContext,
)
from study_agent.domain._validation import JsonObject


def decide_artifacts(
    service: ArtifactService,
    raw_decisions: object,
    context: ExecutionContext,
    expected_sequence: int,
) -> ArtifactBulkDecisionReceipt:
    """Validate one transport-neutral manifest and commit it atomically."""

    decisions = parse_artifact_decisions(raw_decisions)
    return service.record_human_decision_batch(decisions, context, expected_sequence)


def parse_artifact_decisions(raw: object) -> tuple[ArtifactDecisionRequest, ...]:
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes, bytearray)):
        raise ValueError("artifact decisions must contain 1..24 items")
    if not 1 <= len(raw) <= 24:
        raise ValueError("artifact decisions must contain 1..24 items")
    decisions: list[ArtifactDecisionRequest] = []
    for item in raw:
        if (
            not isinstance(item, Mapping)
            or set(item) - {"revision_id", "decision", "supersedes_revision_id"}
            or "revision_id" not in item
            or "decision" not in item
        ):
            raise ValueError("artifact decision item is invalid")
        revision_id = item.get("revision_id")
        decision_value = item.get("decision")
        supersedes = item.get("supersedes_revision_id")
        if not isinstance(revision_id, str) or not revision_id.strip():
            raise ValueError("artifact revision id is invalid")
        decision = {
            "accepted": ArtifactDecision.ACCEPT,
            "accept": ArtifactDecision.ACCEPT,
            "rejected": ArtifactDecision.REJECT,
            "reject": ArtifactDecision.REJECT,
        }.get(decision_value if isinstance(decision_value, str) else "")
        if decision is None or (supersedes is not None and not isinstance(supersedes, str)):
            raise ValueError("artifact decision item is invalid")
        decisions.append(
            ArtifactDecisionRequest(
                ArtifactRevisionId(revision_id),
                decision,
                ArtifactRevisionId(supersedes) if supersedes else None,
            )
        )
    return tuple(decisions)


def artifact_bulk_receipt_payload(receipt: ArtifactBulkDecisionReceipt) -> JsonObject:
    return {
        "bulk_key": receipt.bulk_key,
        "course_id": str(receipt.course_id),
        "session_id": str(receipt.session_id),
        "request_fingerprint": receipt.request_fingerprint,
        "manifest_fingerprint": receipt.manifest_fingerprint,
        "start_sequence": receipt.start_sequence,
        "end_sequence": receipt.end_sequence,
        "results": tuple(
            {
                "ordinal": item.ordinal,
                "revision_id": str(item.revision_id),
                "decision": item.decision.value,
                "supersedes_revision_id": (
                    None
                    if item.supersedes_revision_id is None
                    else str(item.supersedes_revision_id)
                ),
                "event_id": str(item.event_id),
                "sequence": item.sequence,
            }
            for item in receipt.results
        ),
    }


__all__ = [
    "artifact_bulk_receipt_payload",
    "decide_artifacts",
    "parse_artifact_decisions",
]
