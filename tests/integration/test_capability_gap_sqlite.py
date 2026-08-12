from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path

import pytest

from study_agent.adapters.sqlite.capability_gap_store import (
    SQLiteCapabilityGapStore,
    UnsupportedSQLiteCapabilityGapDatabaseError,
)
from study_agent.adapters.sqlite.event_store import (
    SQLiteConnectionIdentityError,
    _writable_nofollow_uri,
)
from cardine.feedback import (
    CapabilityGapAggregate,
    CapabilityGapCorruptionError,
    CapabilityGapObservation,
    CapabilityGapService,
    CapabilityGapUnavailableError,
    CapabilityGapValidationError,
    CapabilityGapWriteContext,
    GapCategory,
    ImpactKind,
    RequestedOperationKind,
    SafeTargetKind,
    proposal_for,
    report_id_for,
)


def _observation() -> CapabilityGapObservation:
    return CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.EXTRACT_TEXT,
        SafeTargetKind.PDF,
        ImpactKind.BLOCKED,
    )


def _context(seed: str) -> CapabilityGapWriteContext:
    return CapabilityGapWriteContext(
        "harness@1",
        "corr@1",
        seed * 64,
        datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
    )


def test_process_restart_preserves_canonical_aggregate(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    first = CapabilityGapService(SQLiteCapabilityGapStore(database))
    recorded = first.record(_observation(), _context("a"))
    payload = first.get(recorded.gap_key)
    restarted = CapabilityGapService(SQLiteCapabilityGapStore(database))
    assert restarted.get(recorded.gap_key) == payload


def test_sqlite_race_converges_to_one_increment_per_report(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    service = CapabilityGapService(SQLiteCapabilityGapStore(database))
    observation = _observation()
    contexts = tuple(_context(seed) for seed in "01234567")

    def write(context: CapabilityGapWriteContext) -> str:
        return service.record(observation, context).gap_key.value

    with ThreadPoolExecutor(max_workers=len(contexts)) as pool:
        keys = tuple(pool.map(write, contexts))
    assert len(set(keys)) == 1
    assert service.get(keys[0]).occurrence_count == len(contexts)


def test_missing_and_tampered_rows_use_safe_errors_only(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    store = SQLiteCapabilityGapStore(database)
    with pytest.raises(CapabilityGapUnavailableError, match="gap_not_found"):
        store.load("a" * 64)
    service = CapabilityGapService(store)
    recorded = service.record(_observation(), _context("a"))
    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE capability_gap_aggregates SET payload = ? WHERE gap_key = ?",
            (b'{"forged":true}', recorded.gap_key.value),
        )
    with pytest.raises(Exception) as error:
        store.load(recorded.gap_key.value)
    assert "forged" not in str(error.value)


def test_store_rejects_noncanonical_proposals_before_mutation(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    store = SQLiteCapabilityGapStore(database)
    service = CapabilityGapService(store)
    recorded = service.record(_observation(), _context("a"))
    aggregate = CapabilityGapAggregate.from_bytes(store.load(recorded.gap_key.value))
    forged = dict(aggregate.to_json())
    forged["occurrence_count"] = True
    with pytest.raises(CapabilityGapCorruptionError):
        store.create_or_increment(recorded.gap_key.value, "b" * 64, str(forged).encode())


def test_default_store_rejects_final_symlink_and_replacement(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    store = SQLiteCapabilityGapStore(database)
    link = tmp_path / "gaps-link.sqlite3"
    link.symlink_to(database)
    with pytest.raises(UnsupportedSQLiteCapabilityGapDatabaseError):
        SQLiteCapabilityGapStore(link)

    replacement = tmp_path / "replacement.sqlite3"
    replacement.touch()
    database.replace(replacement)
    with pytest.raises(SQLiteConnectionIdentityError):
        store.load("a" * 64)


def test_regular_file_swap_during_open_is_rejected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "gaps.sqlite3"
    store = SQLiteCapabilityGapStore(database)
    replacement = tmp_path / "replacement.sqlite3"
    replacement.touch()
    original = _writable_nofollow_uri

    def swap_before_open(path: str) -> str:
        database.replace(replacement)
        return original(path)

    monkeypatch.setattr(
        "study_agent.adapters.sqlite.capability_gap_store._writable_nofollow_uri",
        swap_before_open,
    )
    with pytest.raises(SQLiteConnectionIdentityError):
        store.load("a" * 64)


def test_schema_trigger_drift_is_rejected_before_read(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    store = SQLiteCapabilityGapStore(database)
    with sqlite3.connect(database) as connection:
        connection.execute(
            "CREATE TRIGGER hostile AFTER INSERT ON capability_gap_reports BEGIN SELECT 1; END"
        )
    with pytest.raises(CapabilityGapCorruptionError):
        store.load("a" * 64)
    with pytest.raises(CapabilityGapCorruptionError):
        CapabilityGapService(store).record(_observation(), _context("a"))


def test_existing_report_requires_exact_aggregate_variant(tmp_path: Path) -> None:
    database = tmp_path / "gaps.sqlite3"
    store = SQLiteCapabilityGapStore(database)
    service = CapabilityGapService(store)
    first = service.record(_observation(), _context("a"))
    changed = CapabilityGapObservation(
        GapCategory.INPUT_FORMAT,
        RequestedOperationKind.EXTRACT_TEXT,
        SafeTargetKind.PDF,
        ImpactKind.DEGRADED,
    )
    proposal = proposal_for(changed, _context("a"))
    report_id = report_id_for(first.gap_key, _context("a").idempotency_fingerprint)
    with pytest.raises(CapabilityGapValidationError, match="aggregate_variant_unsupported"):
        store.create_or_increment(first.gap_key.value, report_id, proposal.to_bytes())


def test_legacy_report_schema_fails_explicitly_without_silent_migration(tmp_path: Path) -> None:
    database = tmp_path / "legacy.sqlite3"
    with sqlite3.connect(database) as connection:
        connection.executescript(
            """
            CREATE TABLE capability_gap_aggregates (
                gap_key TEXT PRIMARY KEY,
                payload BLOB NOT NULL
            ) STRICT;
            CREATE TABLE capability_gap_reports (
                report_id TEXT PRIMARY KEY,
                gap_key TEXT NOT NULL
            ) STRICT;
            CREATE INDEX capability_gap_reports_gap_key_idx
            ON capability_gap_reports (gap_key);
            """
        )
    with pytest.raises(CapabilityGapCorruptionError, match="gap_store_schema_invalid"):
        SQLiteCapabilityGapStore(database)
