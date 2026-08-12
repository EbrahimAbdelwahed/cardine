from __future__ import annotations

import json
from dataclasses import replace

import pytest

from cardine.hosts import TutorPresentationReceipt
from study_agent.domain import TutorPresentationKind

SHA_A = "a" * 64
SHA_B = "b" * 64


def _message(**changes: object) -> TutorPresentationReceipt:
    values: dict[str, object] = {
        "host_turn_id": "turn-1",
        "kind": TutorPresentationKind.ASSISTANT_MESSAGE,
        "content": "Start with the valve anatomy.",
        "observed_host_context_sequence": 7,
        "host_context_fingerprint": SHA_A,
        "decision_fingerprint": SHA_B,
    }
    values.update(changes)
    return TutorPresentationReceipt(**values)  # type: ignore[arg-type]


def test_receipt_round_trip_is_canonical_and_fingerprint_binds_every_field() -> None:
    receipt = _message()

    assert TutorPresentationReceipt.from_bytes(receipt.to_bytes()) == receipt
    assert receipt.to_bytes() == receipt.to_bytes()
    assert receipt.fingerprint != replace(receipt, content="A different message.").fingerprint
    assert receipt.fingerprint != replace(receipt, observed_host_context_sequence=8).fingerprint
    assert receipt.fingerprint != replace(receipt, decision_fingerprint="c" * 64).fingerprint

    raw = json.loads(receipt.to_bytes())
    raw["extra"] = True
    with pytest.raises(ValueError, match="invalid field set"):
        TutorPresentationReceipt.from_bytes(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )

    with pytest.raises(ValueError, match="canonical"):
        TutorPresentationReceipt.from_bytes(receipt.to_bytes() + b" ")


def test_continuation_receipt_requires_safe_descriptor_and_rejects_message_descriptors() -> None:
    with pytest.raises(ValueError, match="continuation identity"):
        TutorPresentationReceipt(
            "turn-1",
            TutorPresentationKind.CONTINUATION_REQUEST,
            "Confirm?",
            7,
            SHA_A,
            SHA_B,
        )

    continuation = TutorPresentationReceipt(
        "turn-1",
        TutorPresentationKind.CONTINUATION_REQUEST,
        "Confirm?",
        7,
        SHA_A,
        SHA_B,
        SHA_A,
        "grounding.ask@1.0.0",
        {"type": "boolean"},
    )
    assert TutorPresentationReceipt.from_bytes(continuation.to_bytes()) == continuation

    with pytest.raises(ValueError, match="only continuation"):
        _message(continuation_fingerprint=SHA_A)


def test_receipt_codec_rejects_tampered_fingerprints_and_provider_schema() -> None:
    raw = json.loads(_message().to_bytes())
    raw["host_context_fingerprint"] = "not-a-sha"
    with pytest.raises(ValueError, match="SHA-256"):
        TutorPresentationReceipt.from_bytes(
            json.dumps(raw, sort_keys=True, separators=(",", ":")).encode()
        )

    with pytest.raises(ValueError, match="provider"):
        _message(
            kind=TutorPresentationKind.CONTINUATION_REQUEST,
            content="Confirm?",
            continuation_fingerprint=SHA_A,
            capability_identity="grounding.ask@1.0.0",
            response_schema={"type": "object", "x-provider": True},
        )
