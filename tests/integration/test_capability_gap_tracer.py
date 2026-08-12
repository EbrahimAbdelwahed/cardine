from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from study_agent.adapters.sqlite.capability_gap_store import SQLiteCapabilityGapStore
from cardine.feedback import (
    CapabilityGapHostContext,
    CapabilityGapHostTool,
    CapabilityGapProposal,
    CapabilityGapService,
    GapCategory,
    GapDisposition,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    TrustedLimitationCode,
    TrustedLimitationReceipt,
    WorkaroundSuggestionKind,
)


def _proposal() -> CapabilityGapProposal:
    return CapabilityGapProposal(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.PRESERVE_TABLES,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
        WorkaroundSuggestionKind.USE_SUPPORTED_FORMAT,
    )


def _context(seed: str, observed_at: datetime) -> CapabilityGapHostContext:
    receipt = TrustedLimitationReceipt(
        "pdf-reader@1", 1, TrustedLimitationCode.UNSUPPORTED_FORMAT, "a" * 64
    )
    return CapabilityGapHostContext(
        "harness@1",
        "pdf-reader@1",
        1,
        "corr@1",
        seed * 64,
        observed_at,
        receipt,
    )


def test_unsupported_pdf_tables_vertical_proof_survives_restart(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    at = datetime(2026, 7, 18, 12, 0, tzinfo=UTC)
    tool = CapabilityGapHostTool(
        CapabilityGapService(SQLiteCapabilityGapStore(database))
    )
    first = tool.report(_proposal(), _context("a", at))
    retry = tool.report(_proposal(), _context("a", at))
    second = tool.report(_proposal(), _context("b", at + timedelta(seconds=1)))
    assert first.disposition is GapDisposition.RECORDED
    assert retry.disposition is GapDisposition.DEDUPLICATED
    assert second.disposition is GapDisposition.RECORDED
    assert first.report_id == retry.report_id
    assert first.gap_key == second.gap_key
    assert second.occurrence_count == 2

    restarted = CapabilityGapHostTool(
        CapabilityGapService(SQLiteCapabilityGapStore(database))
    )
    assert restarted.report(_proposal(), _context("b", at)).occurrence_count == 2
