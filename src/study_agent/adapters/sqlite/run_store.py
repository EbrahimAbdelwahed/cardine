"""Durable SQLite compare-and-set storage for operational playbook runs."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from study_agent.domain.identifiers import RunId

from .event_store import SQLiteConnectionGuard, _serialized_sqlite_open, _writable_nofollow_uri


class UnsupportedSQLiteRunDatabaseError(ValueError):
    """The run store requires a durable path-backed SQLite database."""


class RunStoreCorruptionError(RuntimeError):
    """A persisted run row does not satisfy the run-store byte contract."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS playbook_runs (
    run_id TEXT PRIMARY KEY,
    payload BLOB NOT NULL
) STRICT;
"""


class SQLiteRunStore:
    """Path-backed operational run storage with atomic CAS transitions."""

    def __init__(
        self,
        database: str | Path,
        *,
        connection_identity_guard: SQLiteConnectionGuard | None = None,
    ) -> None:
        self._database = str(database)
        normalized = self._database.strip().lower()
        if not normalized or normalized == ":memory:" or normalized.startswith("file:"):
            raise UnsupportedSQLiteRunDatabaseError(
                "SQLiteRunStore requires a path-backed database"
            )
        self._connection_identity_guard = connection_identity_guard
        with closing(self._connect()) as connection:
            try:
                connection.executescript(_SCHEMA)
            except sqlite3.DatabaseError as error:
                raise RunStoreCorruptionError(
                    "playbook_runs schema cannot be initialized"
                ) from error
            self._validate_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        if self._connection_identity_guard is None:
            connection = _serialized_sqlite_open(
                lambda: sqlite3.connect(self._database, isolation_level=None, timeout=30)
            )
        else:
            uri = _writable_nofollow_uri(self._database)
            connection = self._connection_identity_guard.connect(
                lambda: sqlite3.connect(
                    uri, isolation_level=None, timeout=30, uri=True
                )
            )
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        table_rows = connection.execute("PRAGMA table_list").fetchall()
        matching = [row for row in table_rows if row[1] == "playbook_runs"]
        if len(matching) != 1:
            raise RunStoreCorruptionError("playbook_runs must be exactly one SQLite table")
        table = matching[0]
        if table[2] != "table" or int(table[4]) != 0 or int(table[5]) != 1:
            raise RunStoreCorruptionError(
                "playbook_runs must be a rowid-backed STRICT SQLite table"
            )

        columns = connection.execute("PRAGMA table_xinfo(playbook_runs)").fetchall()
        expected = (
            (0, "run_id", "TEXT", 1, None, 1, 0),
            (1, "payload", "BLOB", 1, None, 0, 0),
        )
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
            raise RunStoreCorruptionError("playbook_runs schema is incompatible")

    def create(self, run_id: RunId, payload: bytes) -> bool:
        _require_bytes(payload, "payload")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                "INSERT OR IGNORE INTO playbook_runs (run_id, payload) VALUES (?, ?)",
                (str(run_id), payload),
            )
            return cursor.rowcount == 1

    def compare_and_set(
        self, run_id: RunId, expected: bytes, replacement: bytes
    ) -> bool:
        _require_bytes(expected, "expected")
        _require_bytes(replacement, "replacement")
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                UPDATE playbook_runs
                SET payload = ?
                WHERE run_id = ? AND payload = ?
                """,
                (replacement, str(run_id), expected),
            )
            return cursor.rowcount == 1

    def load(self, run_id: RunId) -> bytes:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT payload, typeof(payload) FROM playbook_runs WHERE run_id = ?",
                (str(run_id),),
            ).fetchone()
        if row is None:
            raise KeyError(run_id)
        if row[1] != "blob" or not isinstance(row[0], bytes):
            raise RunStoreCorruptionError(f"run {run_id} payload is not a SQLite BLOB")
        return row[0]


def _require_bytes(value: object, name: str) -> None:
    if not isinstance(value, bytes):
        raise TypeError(f"{name} must be bytes")
