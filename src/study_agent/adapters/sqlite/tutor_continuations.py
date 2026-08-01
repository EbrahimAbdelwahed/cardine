"""Durable, opaque SQLite storage for tutor continuations.

Continuation payloads are operational host material.  They deliberately do
not participate in the canonical event stream; only their bounded public
descriptor is carried by a validated tutor presentation receipt.
"""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import Callable
from contextlib import closing
from pathlib import Path
from threading import Lock
from urllib.parse import quote

from study_agent.domain import CourseId, SessionId

from .event_store import (
    SQLiteConnectionGuard,
    SQLiteConnectionIdentityError,
    SQLiteConnectionIdentityGuard,
)

MAX_TUTOR_CONTINUATION_BYTES = 65_536


class UnsupportedSQLiteTutorContinuationDatabaseError(ValueError):
    """The continuation store requires a durable, path-backed database."""


class TutorContinuationCorruptionError(RuntimeError):
    """A persisted continuation row does not satisfy the byte contract."""


_SCHEMA = """
CREATE TABLE IF NOT EXISTS tutor_continuations (
    course_id TEXT NOT NULL,
    session_id TEXT NOT NULL,
    continuation_fingerprint TEXT NOT NULL,
    payload BLOB NOT NULL,
    PRIMARY KEY (course_id, session_id, continuation_fingerprint)
) STRICT;
"""


class SQLiteTutorContinuationStore:
    """Path-backed exact-key storage for opaque host continuation bytes."""

    def __init__(
        self,
        database: str | Path,
        *,
        max_payload_bytes: int = MAX_TUTOR_CONTINUATION_BYTES,
        connection_identity_guard: SQLiteConnectionGuard | None = None,
    ) -> None:
        self._database = str(database)
        normalized = self._database.strip().lower()
        if not normalized or normalized == ":memory:" or normalized.startswith("file:"):
            raise UnsupportedSQLiteTutorContinuationDatabaseError(
                "path_backed_database_required"
            )
        if type(max_payload_bytes) is not int or max_payload_bytes < 1:
            raise ValueError("max_payload_bytes must be a positive integer")
        self._max_payload_bytes = max_payload_bytes
        delegate = connection_identity_guard or _guard_for_database(Path(self._database))
        self._connection_identity_guard = _SerializedConnectionGuard(delegate)
        with closing(self._connect()) as connection:
            try:
                connection.executescript(_SCHEMA)
            except sqlite3.DatabaseError as error:
                raise TutorContinuationCorruptionError(
                    "tutor_continuations schema cannot be initialized"
                ) from error
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
        rows = connection.execute("PRAGMA table_list").fetchall()
        matching = [row for row in rows if row[1] == "tutor_continuations"]
        if len(matching) != 1:
            raise TutorContinuationCorruptionError("tutor_continuations must be one table")
        table = matching[0]
        if table[2] != "table" or int(table[4]) != 0 or int(table[5]) != 1:
            raise TutorContinuationCorruptionError("tutor_continuations must be STRICT")
        expected = (
            (0, "course_id", "TEXT", 1, None, 1, 0),
            (1, "session_id", "TEXT", 1, None, 2, 0),
            (2, "continuation_fingerprint", "TEXT", 1, None, 3, 0),
            (3, "payload", "BLOB", 1, None, 0, 0),
        )
        columns = connection.execute("PRAGMA table_xinfo(tutor_continuations)").fetchall()
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
            raise TutorContinuationCorruptionError("tutor_continuations schema is incompatible")

    def create(
        self,
        course_id: CourseId,
        session_id: SessionId,
        continuation_fingerprint: str,
        payload: bytes,
    ) -> bool:
        _validate_key(course_id, session_id, continuation_fingerprint)
        self._validate_payload(payload)
        with closing(self._connect()) as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO tutor_continuations
                    (course_id, session_id, continuation_fingerprint, payload)
                VALUES (?, ?, ?, ?)
                """,
                (str(course_id), str(session_id), continuation_fingerprint, payload),
            )
            return cursor.rowcount == 1

    def load(
        self,
        course_id: CourseId,
        session_id: SessionId,
        continuation_fingerprint: str,
    ) -> bytes:
        _validate_key(course_id, session_id, continuation_fingerprint)
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT payload, typeof(payload)
                FROM tutor_continuations
                WHERE course_id = ? AND session_id = ? AND continuation_fingerprint = ?
                """,
                (str(course_id), str(session_id), continuation_fingerprint),
            ).fetchone()
        if row is None:
            raise KeyError(continuation_fingerprint)
        if row[1] != "blob" or not isinstance(row[0], bytes):
            raise TutorContinuationCorruptionError("continuation payload is not a SQLite BLOB")
        self._validate_payload(row[0])
        return bytes(row[0])

    def delete(
        self,
        course_id: CourseId,
        session_id: SessionId,
        continuation_fingerprint: str,
    ) -> None:
        _validate_key(course_id, session_id, continuation_fingerprint)
        with closing(self._connect()) as connection:
            connection.execute(
                """
                DELETE FROM tutor_continuations
                WHERE course_id = ? AND session_id = ? AND continuation_fingerprint = ?
                """,
                (str(course_id), str(session_id), continuation_fingerprint),
            )

    def _validate_payload(self, payload: object) -> None:
        if not isinstance(payload, bytes):
            raise TypeError("payload must be bytes")
        if not payload:
            raise ValueError("payload must not be empty")
        if len(payload) > self._max_payload_bytes:
            raise ValueError("payload exceeds continuation byte bound")


# The shorter name is useful at composition boundaries and preserves the
# protocol's vocabulary without shadowing the protocol itself.
TutorContinuationStore = SQLiteTutorContinuationStore


def _validate_key(course_id: object, session_id: object, fingerprint: object) -> None:
    if not isinstance(course_id, CourseId) or not isinstance(session_id, SessionId):
        raise TypeError("continuation store requires typed course and session ids")
    if not isinstance(fingerprint, str) or len(fingerprint) != 64:
        raise ValueError("continuation fingerprint must be a SHA-256 digest")
    if any(character not in "0123456789abcdef" for character in fingerprint):
        raise ValueError("continuation fingerprint must be a SHA-256 digest")


def _writable_nofollow_uri(database: str) -> str:
    path = Path(database)
    base = (
        path.as_uri()
        if path.is_absolute()
        else f"file:{quote(path.as_posix(), safe='/')}"
    )
    return f"{base}?mode=rw&nofollow=1"


def _guard_for_database(path: Path) -> SQLiteConnectionGuard:
    if not hasattr(os, "O_NOFOLLOW"):
        raise UnsupportedSQLiteTutorContinuationDatabaseError("nofollow_unavailable")
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
                raise UnsupportedSQLiteTutorContinuationDatabaseError("regular_file_required")
            identity = (metadata.st_dev, metadata.st_ino)
        finally:
            os.close(descriptor)
    except (FileExistsError, OSError) as error:
        raise UnsupportedSQLiteTutorContinuationDatabaseError(
            "safe_database_binding_failed"
        ) from error

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
    """Serialize descriptor snapshots while SQLite opens a guarded handle."""

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
        return _LockedConnection(connection, self._lock)


class _LockedConnection:
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


__all__ = [
    "MAX_TUTOR_CONTINUATION_BYTES",
    "SQLiteTutorContinuationStore",
    "TutorContinuationCorruptionError",
    "TutorContinuationStore",
    "UnsupportedSQLiteTutorContinuationDatabaseError",
]
