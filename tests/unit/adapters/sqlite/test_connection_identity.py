"""Guarded SQLite opens must exclude other adapters from their FD snapshot."""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event

import pytest

from study_agent.adapters.sqlite.event_store import (
    SQLiteConnectionIdentityError,
    SQLiteConnectionIdentityGuard,
    _new_regular_identities,
    _serialized_sqlite_open,
)


def _identity(path: Path) -> tuple[int, int]:
    metadata = path.stat()
    return metadata.st_dev, metadata.st_ino


def _database(path: Path) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE example (value INTEGER)")


@pytest.mark.parametrize("guarded_second", (False, True))
def test_open_windows_are_serialized_across_guard_instances(
    tmp_path: Path, guarded_second: bool
) -> None:
    first_path = tmp_path / "first.sqlite3"
    second_path = tmp_path / "second.sqlite3"
    _database(first_path)
    _database(second_path)
    first_entered = Event()
    second_attempted = Event()
    second_entered = Event()
    release_first = Event()
    first_guard = SQLiteConnectionIdentityGuard(_identity(first_path), lambda: None)
    second_guard = SQLiteConnectionIdentityGuard(_identity(second_path), lambda: None)

    def first_opener() -> sqlite3.Connection:
        first_entered.set()
        assert release_first.wait(5)
        return sqlite3.connect(first_path)

    def second_opener() -> sqlite3.Connection:
        second_entered.set()
        return sqlite3.connect(second_path)

    def first_read() -> int:
        connection = first_guard.connect(first_opener)
        try:
            return int(connection.execute("PRAGMA schema_version").fetchone()[0])
        finally:
            connection.close()

    def second_read() -> int:
        second_attempted.set()
        connection = (
            second_guard.connect(second_opener)
            if guarded_second
            else _serialized_sqlite_open(second_opener)
        )
        try:
            return int(connection.execute("PRAGMA schema_version").fetchone()[0])
        finally:
            connection.close()

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(first_read)
        try:
            assert first_entered.wait(5)
            second = pool.submit(second_read)
            assert second_attempted.wait(5)
            assert not second_entered.wait(0.1)
        finally:
            release_first.set()
        assert first.result(timeout=5) == 1
        assert second.result(timeout=5) == 1


def test_wrong_database_is_rejected_and_connection_is_closed(tmp_path: Path) -> None:
    expected = tmp_path / "expected.sqlite3"
    wrong = tmp_path / "wrong.sqlite3"
    _database(expected)
    _database(wrong)
    opened: list[sqlite3.Connection] = []

    def opener() -> sqlite3.Connection:
        connection = sqlite3.connect(wrong)
        opened.append(connection)
        return connection

    guard = SQLiteConnectionIdentityGuard(_identity(expected), lambda: None)
    with pytest.raises(SQLiteConnectionIdentityError):
        guard.connect(opener)
    assert len(opened) == 1
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        opened[0].execute("SELECT 1")


def test_reused_descriptor_with_different_inode_is_a_new_binding() -> None:
    assert _new_regular_identities({7: (1, 10)}, {7: (1, 20)}) == ((1, 20),)


@pytest.mark.parametrize("persistent", (False, True))
def test_ambiguous_snapshots_close_connections_before_bounded_reprobe(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, persistent: bool
) -> None:
    path = tmp_path / "authorized.sqlite3"
    _database(path)
    identity = _identity(path)
    attempts: list[sqlite3.Connection] = []
    snapshots = 0

    def snapshot() -> dict[int, tuple[int, int] | None]:
        nonlocal snapshots
        snapshots += 1
        if snapshots % 2:
            return {}
        if persistent or len(attempts) == 1:
            return {7: identity, 8: (identity[0], identity[1] + 1)}
        return {7: identity}

    def opener() -> sqlite3.Connection:
        if attempts:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                attempts[-1].execute("SELECT 1")
        connection = sqlite3.connect(path)
        attempts.append(connection)
        return connection

    monkeypatch.setattr(
        "study_agent.adapters.sqlite.event_store._live_file_descriptors", snapshot
    )
    guard = SQLiteConnectionIdentityGuard(identity, lambda: None)
    if persistent:
        with pytest.raises(SQLiteConnectionIdentityError):
            guard.connect(opener)
        assert len(attempts) == 4
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            attempts[-1].execute("SELECT 1")
    else:
        connection = guard.connect(opener)
        try:
            assert len(attempts) == 2
            assert connection.execute("SELECT 1").fetchone() == (1,)
        finally:
            connection.close()


def test_owner_failure_does_not_retry_or_open_a_connection() -> None:
    calls = 0

    def verify_owner() -> None:
        nonlocal calls
        calls += 1
        raise SQLiteConnectionIdentityError("owner changed")

    def opener() -> sqlite3.Connection:
        raise AssertionError("owner failure must prevent opening")

    guard = SQLiteConnectionIdentityGuard((1, 2), verify_owner)
    with pytest.raises(SQLiteConnectionIdentityError, match="owner changed"):
        guard.connect(opener)
    assert calls == 1
