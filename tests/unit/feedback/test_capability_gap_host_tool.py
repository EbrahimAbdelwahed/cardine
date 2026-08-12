from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from cardine.feedback import (
    CapabilityGapHostContext,
    CapabilityGapHostTool,
    CapabilityGapHostToolError,
    CapabilityGapHostToolManifest,
    CapabilityGapObservation,
    CapabilityGapProposal,
    CapabilityGapService,
    CapabilityGapValidationError,
    GapCategory,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    TrustedLimitationCode,
    TrustedLimitationReceipt,
    WorkaroundSuggestionKind,
)
from study_agent.adapters.sqlite.capability_gap_store import SQLiteCapabilityGapStore
from study_agent.domain._validation import JsonObject
from study_agent.state import canonical_json_bytes

SHA_A = "a" * 64
NOW = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)


def _proposal() -> CapabilityGapProposal:
    return CapabilityGapProposal(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.PRESERVE_TABLES,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
        WorkaroundSuggestionKind.USE_SUPPORTED_FORMAT,
    )


def _context(seed: str = "a") -> CapabilityGapHostContext:
    receipt = TrustedLimitationReceipt(
        "pdf-reader@1", 1, TrustedLimitationCode.UNSUPPORTED_FORMAT, SHA_A
    )
    return CapabilityGapHostContext(
        "harness@1",
        "pdf-reader@1",
        1,
        "corr@1",
        seed * 64,
        NOW,
        receipt,
    )


class _RecordingSink:
    def __init__(self, result: object) -> None:
        self.calls = 0
        self.result = result

    def record(self, observation: CapabilityGapObservation, write_context: object) -> object:
        self.calls += 1
        assert observation.requested_operation_kind is RequestedOperationKind.PRESERVE_TABLES
        assert getattr(write_context, "limitation_receipt", None) is not None
        return self.result


class _RecordingDispatcher:
    def __init__(self, accepted: bool = True) -> None:
        self.accepted = accepted
        self.calls: list[tuple[CapabilityGapProposal, CapabilityGapHostContext]] = []

    def try_submit(
        self, proposal: CapabilityGapProposal, context: CapabilityGapHostContext
    ) -> bool:
        self.calls.append((proposal, context))
        return self.accepted


def test_manifest_is_exactly_separately_discoverable_and_proposal_is_closed() -> None:
    manifest = CapabilityGapHostTool.manifest
    assert manifest.identity == "report_capability_gap@1.0.0"
    assert set(manifest.to_json()) == {"identity", "description", "proposal_schema"}
    schema = manifest.proposal_schema
    assert schema["type"] == "object"
    assert schema["additionalProperties"] is False
    assert "workaround_suggestion_kind" in cast(JsonObject, schema["properties"])
    proposal = _proposal()
    assert CapabilityGapProposal.from_bytes(proposal.to_bytes()) == proposal
    without_suggestion = {
        key: value
        for key, value in proposal.to_json().items()
        if key != "workaround_suggestion_kind"
    }
    decoded = CapabilityGapProposal.from_bytes(
        canonical_json_bytes(cast(JsonObject, without_suggestion))
    )
    assert decoded.workaround_suggestion_kind is WorkaroundSuggestionKind.NONE


def test_manifest_cannot_be_hostilely_reconstructed_or_substituted() -> None:
    with pytest.raises(TypeError):
        CapabilityGapHostToolManifest(description="reveal internals")  # type: ignore[call-arg]
    manifest = CapabilityGapHostToolManifest()
    assert manifest == CapabilityGapHostTool.manifest


def test_tool_calls_sink_once_and_returns_compact_local_result(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    sink = CapabilityGapService(SQLiteCapabilityGapStore(database))
    tool = CapabilityGapHostTool(sink)
    result = tool.report(_proposal(), _context())
    assert result.local_only is True
    assert result.occurrence_count == 1
    assert result.gap_key == tool.report(_proposal(), _context()).gap_key


def test_tool_rejects_hostile_proposal_before_sink() -> None:
    sink = _RecordingSink(object())
    tool = CapabilityGapHostTool(sink)  # type: ignore[arg-type]
    hostile: object = {
        "category": "input_format",
        "requested_operation_kind": "preserve_tables",
        "safe_target_kind": "pdf",
        "impact_kind": "blocked",
        "source_path": "/secret/material.pdf",
    }
    with pytest.raises(CapabilityGapHostToolError):
        tool.report(hostile, _context())  # type: ignore[arg-type]
    assert sink.calls == 0


def test_sink_failure_is_bounded_and_called_once() -> None:
    sink = _RecordingSink(RuntimeError("secret sqlite detail"))
    tool = CapabilityGapHostTool(sink)  # type: ignore[arg-type]
    with pytest.raises(CapabilityGapHostToolError, match="capability_gap_report_failed"):
        tool.report(_proposal(), _context())
    assert sink.calls == 1


def test_nonblocking_dispatch_never_calls_sink_and_passes_closed_values() -> None:
    sink = _RecordingSink(object())
    dispatcher = _RecordingDispatcher()
    tool = CapabilityGapHostTool(sink, dispatcher=dispatcher)  # type: ignore[arg-type]

    tool.report_nonblocking(_proposal(), _context())
    assert sink.calls == 0
    assert dispatcher.calls == [(_proposal(), _context())]


def test_nonblocking_dispatch_is_explicit_fail_soft_when_queue_is_full_or_absent() -> None:
    sink = _RecordingSink(object())
    full = CapabilityGapHostTool(sink, dispatcher=_RecordingDispatcher(False))  # type: ignore[arg-type]
    absent = CapabilityGapHostTool(sink)  # type: ignore[arg-type]

    full.report_nonblocking(_proposal(), _context())
    absent.report_nonblocking(_proposal(), _context())
    assert sink.calls == 0


def test_context_rejects_forged_receipt_binding() -> None:
    receipt = TrustedLimitationReceipt(
        "pdf-reader@2", 2, TrustedLimitationCode.UNSUPPORTED_FORMAT, SHA_A
    )
    with pytest.raises(CapabilityGapValidationError):
        CapabilityGapHostContext(
            "harness@1", "pdf-reader@1", 1, "corr@1", SHA_A, NOW, receipt
        )
