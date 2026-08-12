"""Path-backed SQLite registry for strict capability-gap aggregates."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable, Collection, Mapping
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock
from typing import cast

from cardine.feedback.contracts import (
    CapabilityGapAggregate,
    CapabilityGapCollisionError,
    CapabilityGapCorruptionError,
    CapabilityGapResolution,
    CapabilityGapValidationError,
    GapExportState,
    GapResolutionKind,
)

from .event_store import (
    SQLiteConnectionGuard,
    SQLiteConnectionIdentityError,
    SQLiteConnectionIdentityGuard,
    _writable_nofollow_uri,
)


class UnsupportedSQLiteCapabilityGapDatabaseError(ValueError):
    """The registry requires a durable path-backed SQLite database."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS capability_gap_aggregates (
    gap_key TEXT PRIMARY KEY,
    payload BLOB NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS capability_gap_reports (
    report_id TEXT PRIMARY KEY,
    gap_key TEXT NOT NULL,
    observed_at TEXT NOT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS capability_gap_reports_gap_key_idx
ON capability_gap_reports (gap_key);
"""


class SQLiteCapabilityGapStore:
    """Atomic local registry; it never appends canonical study events."""

    def __init__(
        self,
        database: str | Path,
        *,
        connection_identity_guard: SQLiteConnectionGuard | None = None,
    ) -> None:
        self._database = str(database)
        normalized = self._database.strip().lower()
        if not normalized or normalized == ":memory:" or normalized.startswith("file:"):
            raise UnsupportedSQLiteCapabilityGapDatabaseError("path_backed_database_required")
        self._connection_identity_guard = _SerializedConnectionGuard(
            connection_identity_guard or _guard_for_database(Path(self._database))
        )
        with closing(self._connect()) as connection:
            try:
                connection.executescript(_SCHEMA)
            except sqlite3.DatabaseError:
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            self._validate_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        uri = _writable_nofollow_uri(self._database)
        connection = self._connection_identity_guard.connect(
            lambda: sqlite3.connect(uri, isolation_level=None, timeout=30, uri=True)
        )
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        expected_tables = {"capability_gap_aggregates", "capability_gap_reports"}
        try:
            table_rows = connection.execute("PRAGMA table_list").fetchall()
        except sqlite3.DatabaseError:
            raise CapabilityGapCorruptionError("gap_store_corrupt") from None
        user_rows = [
            row for row in table_rows if row[1] not in {"sqlite_schema", "sqlite_temp_schema"}
        ]
        if {row[1] for row in user_rows} != expected_tables or len(user_rows) != 2:
            raise CapabilityGapCorruptionError("gap_store_schema_invalid")
        for row in user_rows:
            if row[2] != "table" or int(row[4]) != 0 or int(row[5]) != 1:
                raise CapabilityGapCorruptionError("gap_store_schema_invalid")
        expected_columns = {
            "capability_gap_aggregates": (
                (0, "gap_key", "TEXT", 1, None, 1, 0),
                (1, "payload", "BLOB", 1, None, 0, 0),
            ),
            "capability_gap_reports": (
                (0, "report_id", "TEXT", 1, None, 1, 0),
                (1, "gap_key", "TEXT", 1, None, 0, 0),
                (2, "observed_at", "TEXT", 1, None, 0, 0),
            ),
        }
        for table, expected in expected_columns.items():
            columns = connection.execute(f"PRAGMA table_xinfo({table})").fetchall()
            actual = tuple(
                (
                    int(row[0]),
                    row[1],
                    row[2],
                    int(row[3]),
                    row[4],
                    int(row[5]),
                    int(row[6]),
                )
                for row in columns
            )
            if actual != expected:
                raise CapabilityGapCorruptionError("gap_store_schema_invalid")
        try:
            indexes = {
                row[1]
                for table in expected_tables
                for row in connection.execute(f"PRAGMA index_list({table})").fetchall()
            }
            expected_indexes = {
                "sqlite_autoindex_capability_gap_aggregates_1",
                "sqlite_autoindex_capability_gap_reports_1",
                "capability_gap_reports_gap_key_idx",
            }
            if indexes != expected_indexes:
                raise CapabilityGapCorruptionError("gap_store_schema_invalid")
            triggers = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' ORDER BY name"
            ).fetchall()
            if tuple(row[0] for row in triggers) != ():
                raise CapabilityGapCorruptionError("gap_store_schema_invalid")
        except sqlite3.DatabaseError:
            raise CapabilityGapCorruptionError("gap_store_schema_invalid") from None

    def create_or_increment(
        self, gap_key: str, report_id: str, payload: bytes
    ) -> tuple[bytes, bool]:
        encoded, created, _ = self._create_or_increment(
            gap_key, report_id, payload, rate_limit=None
        )
        return encoded, created

    def create_or_increment_rate_limited(
        self,
        gap_key: str,
        report_id: str,
        payload: bytes,
        *,
        max_occurrences: int,
        window_start: datetime,
        observed_at: datetime,
    ) -> tuple[bytes, bool, bool]:
        """Atomically enforce a per-key window and record one report.

        Idempotency is checked before the rate gate in the same transaction so
        an exact retry remains ``DEDUPLICATED`` even when the window is full.
        The window count is derived from report timestamps, never the lifetime
        aggregate occurrence count.
        """

        if type(max_occurrences) is not int or max_occurrences < 1:
            raise CapabilityGapValidationError("invalid_rate_policy")
        if not isinstance(window_start, datetime) or not isinstance(observed_at, datetime):
            raise CapabilityGapValidationError("invalid_rate_policy")
        if (
            window_start.tzinfo is None
            or window_start.utcoffset() is None
            or observed_at.tzinfo is None
            or observed_at.utcoffset() is None
        ):
            raise CapabilityGapValidationError("invalid_rate_policy")
        return self._create_or_increment(
            gap_key,
            report_id,
            payload,
            rate_limit=(
                max_occurrences,
                window_start.astimezone(UTC),
                observed_at.astimezone(UTC),
            ),
        )

    def _create_or_increment(
        self,
        gap_key: str,
        report_id: str,
        payload: bytes,
        *,
        rate_limit: tuple[int, datetime, datetime] | None,
    ) -> tuple[bytes, bool, bool]:
        _validate_digest(gap_key, "gap_key")
        _validate_digest(report_id, "report_id")
        if not isinstance(payload, bytes):
            raise CapabilityGapValidationError("invalid_payload")
        try:
            proposal = CapabilityGapAggregate.from_bytes(payload)
        except (CapabilityGapCorruptionError, CapabilityGapCollisionError):
            raise
        if proposal.gap_key.value != gap_key or proposal.occurrence_count != 1:
            raise CapabilityGapCollisionError("gap_key_collision")
        if proposal.first_seen != proposal.last_seen:
            raise CapabilityGapValidationError("invalid_proposal")

        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                report = connection.execute(
                    "SELECT gap_key, observed_at FROM capability_gap_reports "
                    "WHERE report_id = ?",
                    (report_id,),
                ).fetchone()
                if report is not None:
                    if report[0] != gap_key:
                        raise CapabilityGapCollisionError("gap_key_collision")
                    row = connection.execute(
                        "SELECT payload, typeof(payload) "
                        "FROM capability_gap_aggregates WHERE gap_key = ?",
                        (gap_key,),
                    ).fetchone()
                    if row is None or row[1] != "blob" or not isinstance(row[0], bytes):
                        raise CapabilityGapCorruptionError("gap_store_corrupt")
                    current = CapabilityGapAggregate.from_bytes(bytes(row[0]))
                    _assert_aggregate_matches_proposal(current, proposal)
                    connection.commit()
                    return bytes(row[0]), False, False

                row = connection.execute(
                    "SELECT payload, typeof(payload) "
                    "FROM capability_gap_aggregates WHERE gap_key = ?",
                    (gap_key,),
                ).fetchone()
                if row is None:
                    connection.execute(
                        "INSERT INTO capability_gap_aggregates (gap_key, payload) VALUES (?, ?)",
                        (gap_key, payload),
                    )
                    connection.execute(
                        "INSERT INTO capability_gap_reports "
                        "(report_id, gap_key, observed_at) VALUES (?, ?, ?)",
                        (report_id, gap_key, _timestamp(proposal.first_seen)),
                    )
                    connection.commit()
                    return payload, True, False
                if row[1] != "blob" or not isinstance(row[0], bytes):
                    raise CapabilityGapCorruptionError("gap_store_corrupt")
                current = CapabilityGapAggregate.from_bytes(bytes(row[0]))
                _assert_aggregate_matches_proposal(current, proposal)
                if current.resolution is not GapResolutionKind.UNRESOLVED:
                    raise CapabilityGapValidationError("resolution_closed")
                if rate_limit is not None:
                    max_occurrences, window_start, observed_at = rate_limit
                    window_count = connection.execute(
                        "SELECT COUNT(*) FROM capability_gap_reports "
                        "WHERE gap_key = ? AND observed_at >= ? AND observed_at <= ?",
                        (gap_key, _timestamp(window_start), _timestamp(observed_at)),
                    ).fetchone()[0]
                    if int(window_count) >= max_occurrences:
                        connection.commit()
                        return bytes(row[0]), False, True
                updated = CapabilityGapAggregate(
                    gap_key=current.gap_key,
                    dimensions=current.dimensions,
                    verification_kind=current.verification_kind,
                    impact_kind=current.impact_kind,
                    first_seen=current.first_seen,
                    last_seen=proposal.last_seen,
                    occurrence_count=current.occurrence_count + 1,
                    resolution=current.resolution,
                    export_state=(
                        GapExportState.PENDING
                        if current.export_state is GapExportState.EXPORTED
                        else current.export_state
                    ),
                    resolution_authority_fingerprint=current.resolution_authority_fingerprint,
                    resolved_at=current.resolved_at,
                )
                encoded = updated.to_bytes()
                connection.execute(
                    "UPDATE capability_gap_aggregates SET payload = ? WHERE gap_key = ?",
                    (encoded, gap_key),
                )
                connection.execute(
                    "INSERT INTO capability_gap_reports "
                    "(report_id, gap_key, observed_at) VALUES (?, ?, ?)",
                    (report_id, gap_key, _timestamp(proposal.last_seen)),
                )
                connection.commit()
                return encoded, True, False
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.IntegrityError:
                connection.rollback()
                raise CapabilityGapCollisionError("gap_key_collision") from None
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise

    def load(self, gap_key: str) -> bytes:
        _validate_digest(gap_key, "gap_key")
        with closing(self._connect()) as connection:
            try:
                self._validate_schema(connection)
                row = connection.execute(
                    "SELECT payload, typeof(payload) "
                    "FROM capability_gap_aggregates WHERE gap_key = ?",
                    (gap_key,),
                ).fetchone()
            except sqlite3.DatabaseError:
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
        if row is None:
            from cardine.feedback.contracts import CapabilityGapUnavailableError

            raise CapabilityGapUnavailableError("gap_not_found")
        if row[1] != "blob" or not isinstance(row[0], bytes):
            raise CapabilityGapCorruptionError("gap_store_corrupt")
        try:
            aggregate = CapabilityGapAggregate.from_bytes(bytes(row[0]))
        except (CapabilityGapCorruptionError, CapabilityGapCollisionError):
            raise
        if aggregate.gap_key.value != gap_key:
            raise CapabilityGapCollisionError("gap_key_collision")
        return bytes(row[0])

    def list_aggregates(
        self, *, states: Collection[GapExportState] | None = None
    ) -> tuple[bytes, ...]:
        """Return validated aggregate bytes in deterministic key order.

        This is a read-only snapshot of the operational plane.  It never
        deletes or rewrites source aggregates and deliberately exposes only
        canonical aggregate bytes to the outbox coordinator.
        """

        selected = frozenset(GapExportState) if states is None else frozenset(states)
        if not selected or any(not isinstance(state, GapExportState) for state in selected):
            raise CapabilityGapValidationError("invalid_export_states")
        with closing(self._connect()) as connection:
            try:
                self._validate_schema(connection)
                rows = connection.execute(
                    "SELECT gap_key, payload, typeof(payload) "
                    "FROM capability_gap_aggregates ORDER BY gap_key ASC"
                ).fetchall()
            except sqlite3.DatabaseError:
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
        result: list[bytes] = []
        for key, payload, payload_type in rows:
            if payload_type != "blob" or not isinstance(payload, bytes):
                raise CapabilityGapCorruptionError("gap_store_corrupt")
            try:
                aggregate = CapabilityGapAggregate.from_bytes(bytes(payload))
            except (CapabilityGapCorruptionError, CapabilityGapCollisionError):
                raise
            if aggregate.gap_key.value != key:
                raise CapabilityGapCollisionError("gap_key_collision")
            if aggregate.export_state in selected:
                result.append(bytes(payload))
        return tuple(result)

    def claim_export_batch(self) -> tuple[bytes, ...]:
        """Claim one deterministic batch and return its post-PENDING bytes.

        The claim and every state transition are performed under one
        ``BEGIN IMMEDIATE`` transaction.  Consequently the publisher only
        receives bytes that correspond exactly to rows claimed by this call.
        """

        claimable = frozenset(
            {GapExportState.LOCAL, GapExportState.PENDING, GapExportState.FAILED}
        )
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                rows = connection.execute(
                    "SELECT gap_key, payload, typeof(payload) "
                    "FROM capability_gap_aggregates ORDER BY gap_key ASC"
                ).fetchall()
                claimed: list[bytes] = []
                for key, payload, payload_type in rows:
                    if payload_type != "blob" or not isinstance(payload, bytes):
                        raise CapabilityGapCorruptionError("gap_store_corrupt")
                    current = CapabilityGapAggregate.from_bytes(bytes(payload))
                    if current.gap_key.value != key:
                        raise CapabilityGapCollisionError("gap_key_collision")
                    if current.export_state not in claimable:
                        continue
                    if current.export_state is GapExportState.PENDING:
                        claimed.append(bytes(payload))
                        continue
                    updated = CapabilityGapAggregate(
                        gap_key=current.gap_key,
                        dimensions=current.dimensions,
                        verification_kind=current.verification_kind,
                        impact_kind=current.impact_kind,
                        first_seen=current.first_seen,
                        last_seen=current.last_seen,
                        occurrence_count=current.occurrence_count,
                        resolution=current.resolution,
                        export_state=GapExportState.PENDING,
                        resolution_authority_fingerprint=current.resolution_authority_fingerprint,
                        resolved_at=current.resolved_at,
                    )
                    encoded = updated.to_bytes()
                    connection.execute(
                        "UPDATE capability_gap_aggregates SET payload = ? "
                        "WHERE gap_key = ? AND payload = ?",
                        (encoded, key, payload),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] != 1:
                        raise CapabilityGapCorruptionError("gap_store_claim_lost")
                    claimed.append(encoded)
                connection.commit()
                return tuple(claimed)
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise

    def finalize_export_batch(
        self, expected: Mapping[str, bytes], state: GapExportState
    ) -> tuple[str, ...]:
        """CAS-finalize exactly the unchanged rows from a claimed batch.

        Rows whose aggregate changed after the claim are intentionally left
        pending.  They will be picked up by a later export with fresh bytes.
        """

        if not isinstance(expected, Mapping) or any(
            not isinstance(key, str) or not isinstance(payload, bytes)
            for key, payload in expected.items()
        ):
            raise CapabilityGapValidationError("invalid_export_expectations")
        if state not in {GapExportState.EXPORTED, GapExportState.FAILED}:
            raise CapabilityGapValidationError("invalid_export_final_state")
        items = tuple(sorted(expected.items()))
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                finalized: list[str] = []
                for key, expected_payload in items:
                    _validate_digest(key, "gap_key")
                    expected_aggregate = CapabilityGapAggregate.from_bytes(expected_payload)
                    if expected_aggregate.gap_key.value != key:
                        raise CapabilityGapCollisionError("gap_key_collision")
                    row = connection.execute(
                        "SELECT payload, typeof(payload) FROM capability_gap_aggregates "
                        "WHERE gap_key = ?",
                        (key,),
                    ).fetchone()
                    if row is None:
                        continue
                    if row[1] != "blob" or not isinstance(row[0], bytes):
                        raise CapabilityGapCorruptionError("gap_store_corrupt")
                    current_payload = bytes(row[0])
                    if current_payload != expected_payload:
                        continue
                    current = CapabilityGapAggregate.from_bytes(current_payload)
                    if current.export_state is not GapExportState.PENDING:
                        continue
                    updated = CapabilityGapAggregate(
                        gap_key=current.gap_key,
                        dimensions=current.dimensions,
                        verification_kind=current.verification_kind,
                        impact_kind=current.impact_kind,
                        first_seen=current.first_seen,
                        last_seen=current.last_seen,
                        occurrence_count=current.occurrence_count,
                        resolution=current.resolution,
                        export_state=state,
                        resolution_authority_fingerprint=current.resolution_authority_fingerprint,
                        resolved_at=current.resolved_at,
                    )
                    encoded = updated.to_bytes()
                    connection.execute(
                        "UPDATE capability_gap_aggregates SET payload = ? "
                        "WHERE gap_key = ? AND payload = ?",
                        (encoded, key, expected_payload),
                    )
                    if connection.execute("SELECT changes()").fetchone()[0] == 1:
                        finalized.append(key)
                connection.commit()
                return tuple(finalized)
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise

    # The explicit aliases keep the storage port readable to adapters without
    # introducing a second implementation or a new persistence schema.
    snapshot = list_aggregates
    list_pending = list_aggregates

    def resolve(self, gap_key: str, resolution: CapabilityGapResolution) -> bytes:
        """Atomically apply one trusted terminal resolution exactly once."""

        _validate_digest(gap_key, "gap_key")
        if not isinstance(resolution, CapabilityGapResolution):
            raise CapabilityGapValidationError("invalid_resolution")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                row = connection.execute(
                    "SELECT payload, typeof(payload) FROM capability_gap_aggregates "
                    "WHERE gap_key = ?",
                    (gap_key,),
                ).fetchone()
                if row is None:
                    from cardine.feedback.contracts import CapabilityGapUnavailableError

                    raise CapabilityGapUnavailableError("gap_not_found")
                if row[1] != "blob" or not isinstance(row[0], bytes):
                    raise CapabilityGapCorruptionError("gap_store_corrupt")
                current = CapabilityGapAggregate.from_bytes(bytes(row[0]))
                if current.gap_key.value != gap_key:
                    raise CapabilityGapCollisionError("gap_key_collision")
                if current.resolution is resolution.kind:
                    if current.resolution_authority_fingerprint != resolution.authority_fingerprint:
                        raise CapabilityGapCollisionError("resolution_authority_mismatch")
                    connection.commit()
                    return bytes(row[0])
                if current.resolution is not resolution.kind:
                    if (
                        current.resolution is not resolution.kind
                        and current.resolution is not GapResolutionKind.UNRESOLVED
                    ):
                        raise CapabilityGapValidationError("resolution_already_set")
                    updated = CapabilityGapAggregate(
                        gap_key=current.gap_key,
                        dimensions=current.dimensions,
                        verification_kind=current.verification_kind,
                        impact_kind=current.impact_kind,
                        first_seen=current.first_seen,
                        last_seen=current.last_seen,
                        occurrence_count=current.occurrence_count,
                        resolution=resolution.kind,
                        export_state=(
                            GapExportState.PENDING
                            if current.export_state is GapExportState.EXPORTED
                            else current.export_state
                        ),
                        resolution_authority_fingerprint=resolution.authority_fingerprint,
                        resolved_at=resolution.resolved_at,
                    )
                    encoded = updated.to_bytes()
                    connection.execute(
                        "UPDATE capability_gap_aggregates SET payload = ? WHERE gap_key = ?",
                        (encoded, gap_key),
                    )
                    connection.commit()
                    return encoded
                connection.commit()
                return bytes(row[0])
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise

    def prune(self, before: object) -> int:
        """Delete expired aggregates and their report identities atomically."""

        if not isinstance(before, datetime) or before.tzinfo is None or before.utcoffset() is None:
            raise CapabilityGapValidationError("invalid_retention_boundary")
        cutoff = before.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                rows = connection.execute(
                    "SELECT gap_key, payload, typeof(payload) FROM capability_gap_aggregates"
                ).fetchall()
                expired: list[str] = []
                for key, payload, payload_type in rows:
                    if payload_type != "blob" or not isinstance(payload, bytes):
                        raise CapabilityGapCorruptionError("gap_store_corrupt")
                    aggregate = CapabilityGapAggregate.from_bytes(bytes(payload))
                    seen = aggregate.last_seen.isoformat(timespec="microseconds").replace(
                        "+00:00", "Z"
                    )
                    if seen < cutoff:
                        expired.append(str(key))
                for key in expired:
                    connection.execute(
                        "DELETE FROM capability_gap_reports WHERE gap_key = ?", (key,)
                    )
                    connection.execute(
                        "DELETE FROM capability_gap_aggregates WHERE gap_key = ?", (key,)
                    )
                connection.commit()
                return len(expired)
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise

    def set_export_state(self, gap_key: str, state: GapExportState) -> bytes:
        """Record explicit local export state without changing evidence."""

        _validate_digest(gap_key, "gap_key")
        if not isinstance(state, GapExportState):
            raise CapabilityGapValidationError("invalid_export_state")
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                row = connection.execute(
                    "SELECT payload, typeof(payload) FROM capability_gap_aggregates "
                    "WHERE gap_key = ?",
                    (gap_key,),
                ).fetchone()
                if row is None:
                    from cardine.feedback.contracts import CapabilityGapUnavailableError

                    raise CapabilityGapUnavailableError("gap_not_found")
                if row[1] != "blob" or not isinstance(row[0], bytes):
                    raise CapabilityGapCorruptionError("gap_store_corrupt")
                current = CapabilityGapAggregate.from_bytes(bytes(row[0]))
                if current.gap_key.value != gap_key:
                    raise CapabilityGapCollisionError("gap_key_collision")
                if current.export_state is state:
                    connection.commit()
                    return bytes(row[0])
                allowed: dict[GapExportState, frozenset[GapExportState]] = {
                    GapExportState.LOCAL: frozenset(
                        {GapExportState.PENDING, GapExportState.FAILED}
                    ),
                    GapExportState.PENDING: frozenset(
                        {GapExportState.EXPORTED, GapExportState.FAILED}
                    ),
                    GapExportState.FAILED: frozenset(
                        {GapExportState.PENDING, GapExportState.FAILED}
                    ),
                    GapExportState.EXPORTED: frozenset(),
                }
                if state not in allowed[current.export_state]:
                    raise CapabilityGapValidationError("export_state_transition_invalid")
                updated = CapabilityGapAggregate(
                    gap_key=current.gap_key,
                    dimensions=current.dimensions,
                    verification_kind=current.verification_kind,
                    impact_kind=current.impact_kind,
                    first_seen=current.first_seen,
                    last_seen=current.last_seen,
                    occurrence_count=current.occurrence_count,
                    resolution=current.resolution,
                    export_state=state,
                    resolution_authority_fingerprint=current.resolution_authority_fingerprint,
                    resolved_at=current.resolved_at,
                )
                encoded = updated.to_bytes()
                connection.execute(
                    "UPDATE capability_gap_aggregates SET payload = ? WHERE gap_key = ?",
                    (encoded, gap_key),
                )
                connection.commit()
                return encoded
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise

    def set_export_states(
        self, gap_keys: Collection[str], state: GapExportState
    ) -> tuple[bytes, ...]:
        """Atomically transition several outbox rows in one SQLite transaction.

        The outbox publishes one immutable snapshot.  A single transaction
        prevents a crash or constraint failure from marking only part of that
        snapshot exported, which would make a retry silently change its
        contents.
        """

        if not isinstance(state, GapExportState):
            raise CapabilityGapValidationError("invalid_export_state")
        keys = tuple(gap_keys)
        if any(not isinstance(key, str) for key in keys) or len(keys) != len(set(keys)):
            raise CapabilityGapValidationError("invalid_export_keys")
        for key in keys:
            _validate_digest(key, "gap_key")
        if not keys:
            return ()
        allowed: dict[GapExportState, frozenset[GapExportState]] = {
            GapExportState.LOCAL: frozenset(
                {GapExportState.PENDING, GapExportState.FAILED}
            ),
            GapExportState.PENDING: frozenset(
                {GapExportState.PENDING, GapExportState.EXPORTED, GapExportState.FAILED}
            ),
            GapExportState.FAILED: frozenset(
                {GapExportState.PENDING, GapExportState.FAILED}
            ),
            GapExportState.EXPORTED: frozenset({GapExportState.EXPORTED}),
        }
        with closing(self._connect()) as connection:
            try:
                connection.execute("BEGIN IMMEDIATE")
                self._validate_schema(connection)
                encoded_rows: list[tuple[str, bytes]] = []
                for key in keys:
                    row = connection.execute(
                        "SELECT payload, typeof(payload) FROM capability_gap_aggregates "
                        "WHERE gap_key = ?",
                        (key,),
                    ).fetchone()
                    if row is None:
                        from cardine.feedback.contracts import CapabilityGapUnavailableError

                        raise CapabilityGapUnavailableError("gap_not_found")
                    if row[1] != "blob" or not isinstance(row[0], bytes):
                        raise CapabilityGapCorruptionError("gap_store_corrupt")
                    current = CapabilityGapAggregate.from_bytes(bytes(row[0]))
                    if current.gap_key.value != key:
                        raise CapabilityGapCollisionError("gap_key_collision")
                    if state is current.export_state:
                        encoded_rows.append((key, bytes(row[0])))
                        continue
                    if state not in allowed[current.export_state]:
                        raise CapabilityGapValidationError("export_state_transition_invalid")
                    updated = CapabilityGapAggregate(
                        gap_key=current.gap_key,
                        dimensions=current.dimensions,
                        verification_kind=current.verification_kind,
                        impact_kind=current.impact_kind,
                        first_seen=current.first_seen,
                        last_seen=current.last_seen,
                        occurrence_count=current.occurrence_count,
                        resolution=current.resolution,
                        export_state=state,
                        resolution_authority_fingerprint=current.resolution_authority_fingerprint,
                        resolved_at=current.resolved_at,
                    )
                    encoded_rows.append((key, updated.to_bytes()))
                for key, payload in encoded_rows:
                    connection.execute(
                        "UPDATE capability_gap_aggregates SET payload = ? WHERE gap_key = ?",
                        (payload, key),
                    )
                connection.commit()
                return tuple(payload for _, payload in encoded_rows)
            except (
                CapabilityGapValidationError,
                CapabilityGapCollisionError,
                CapabilityGapCorruptionError,
            ):
                connection.rollback()
                raise
            except sqlite3.DatabaseError:
                connection.rollback()
                raise CapabilityGapCorruptionError("gap_store_corrupt") from None
            except BaseException:
                connection.rollback()
                raise


def _validate_digest(value: object, field: str) -> None:
    import re

    if not isinstance(value, str) or re.fullmatch(r"[0-9a-f]{64}", value) is None:
        raise CapabilityGapValidationError(f"invalid_{field}")


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _assert_aggregate_matches_proposal(
    current: CapabilityGapAggregate, proposal: CapabilityGapAggregate
) -> None:
    """Reject cross-key, cross-dimension, and variant reuse of a report."""

    if current.gap_key != proposal.gap_key or current.dimensions != proposal.dimensions:
        raise CapabilityGapCollisionError("gap_key_collision")
    if (
        current.verification_kind != proposal.verification_kind
        or current.impact_kind != proposal.impact_kind
    ):
        raise CapabilityGapValidationError("aggregate_variant_unsupported")


def _guard_for_database(path: Path) -> SQLiteConnectionGuard:
    """Create the mandatory no-follow identity guard for the default adapter."""

    if not hasattr(os, "O_NOFOLLOW"):
        raise UnsupportedSQLiteCapabilityGapDatabaseError("nofollow_unavailable")
    absolute = path.absolute()
    flags = os.O_RDONLY | os.O_NOFOLLOW
    try:
        try:
            descriptor = os.open(absolute, flags)
        except FileNotFoundError:
            descriptor = os.open(
                absolute,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
            )
        try:
            metadata = os.fstat(descriptor)
            if not stat.S_ISREG(metadata.st_mode):
                raise UnsupportedSQLiteCapabilityGapDatabaseError("regular_file_required")
            identity = (metadata.st_dev, metadata.st_ino)
        finally:
            os.close(descriptor)
    except (FileExistsError, OSError) as error:
        raise UnsupportedSQLiteCapabilityGapDatabaseError("safe_database_binding_failed") from error

    def verify_owner() -> None:
        try:
            current = os.open(absolute, flags)
            try:
                metadata = os.fstat(current)
            finally:
                os.close(current)
        except OSError as error:
            raise SQLiteConnectionIdentityError("database binding changed") from error
        if not stat.S_ISREG(metadata.st_mode) or (metadata.st_dev, metadata.st_ino) != identity:
            raise SQLiteConnectionIdentityError("database binding changed")

    return SQLiteConnectionIdentityGuard(identity, verify_owner)


class _SerializedConnectionGuard:
    """Serialize descriptor snapshots so concurrent opens cannot cross-bind."""

    def __init__(self, delegate: SQLiteConnectionGuard) -> None:
        self._delegate = delegate
        self._lock = Lock()

    def connect(self, opener: Callable[[], sqlite3.Connection]) -> sqlite3.Connection:
        self._lock.acquire()
        try:
            connection = self._delegate.connect(opener)
        except BaseException:
            self._lock.release()
            raise
        return cast(sqlite3.Connection, _LockedConnection(connection, self._lock))


class _LockedConnection:
    """Hold the connection gate until the guarded SQLite handle is closed."""

    def __init__(self, connection: sqlite3.Connection, lock: Lock) -> None:
        self._connection = connection
        self._lock = lock
        self._closed = False

    def __getattr__(self, name: str) -> object:
        return getattr(self._connection, name)

    def close(self) -> None:
        if self._closed:
            return
        try:
            self._connection.close()
        finally:
            self._closed = True
            self._lock.release()


__all__ = ["SQLiteCapabilityGapStore", "UnsupportedSQLiteCapabilityGapDatabaseError"]
