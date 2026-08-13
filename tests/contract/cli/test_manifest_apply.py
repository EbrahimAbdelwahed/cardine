from __future__ import annotations

import json
import os
import socket
import sqlite3
from collections.abc import Callable, Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.cli.lifecycle import LocalLifecycleRuntime
from cardine.cli.main import main
from cardine.cli.registry import (
    NetworkRequirement,
    OperationEffect,
    RepositoryRequirement,
    registration_for,
)
from study_agent.adapters.sqlite import SQLiteEventStore
from study_agent.adapters.sqlite.event_store import SQLiteConnectionGuard
from study_agent.ingestion import IngestionErrorCode, TextIngestionError, TextIngestionService
from study_agent.retrieval import (
    CourseSourceContent,
    SourceContentError,
    SourceContentErrorCode,
)
from study_agent.state import EventRegistry


class _UnreadableEnvironment(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"credential environment was read: {key}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("credential environment was enumerated")

    def __len__(self) -> int:
        raise AssertionError("credential environment length was read")


class _NoNetworkSocket(socket.socket):
    def connect(self, address: object) -> None:
        raise AssertionError(f"manifest apply attempted network access: {address}")

    def connect_ex(self, address: object) -> int:
        raise AssertionError(f"manifest apply attempted network access: {address}")


def _document(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    return cast(dict[str, Any], json.loads(captured.out))


def _write_manifest(root: Path, *, source_title: str = "Notes") -> Path:
    path = root / "study-agent.manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": {"path": "runtime/repository", "model": None},
                "courses": [
                    {
                        "course_id": "course-1",
                        "title": "Course One",
                        "language": "en",
                        "exam_date": None,
                        "learning_goals": ["Explain the supplied notes"],
                        "assessment_styles": [],
                        "sources": [
                            {
                                "source_id": "notes-1",
                                "path": "materials/notes.md",
                                "title": source_title,
                                "trust_level": 80,
                                "source_role": "course_material",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return path


def _plan(path: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    assert main(("--json", "manifest", "plan", str(path))) == 0
    return cast(dict[str, Any], _document(capsys)["data"]["plan"])


def _apply(
    path: Path,
    fingerprint: str,
    capsys: pytest.CaptureFixture[str],
    *,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(path),
                "--expect-plan",
                fingerprint,
            ),
            environment=environment,
        )
        == 0
    )
    return cast(dict[str, Any], _document(capsys)["data"]["receipt"])


def _event_count(repository: Path) -> int:
    return _database_event_count(repository / "state" / "events.sqlite3")


def _database_event_count(database: Path) -> int:
    connection = sqlite3.connect(database)
    try:
        try:
            row = connection.execute("SELECT count(*) FROM events").fetchone()
        except sqlite3.OperationalError as error:
            if "no such table" not in str(error):
                raise
            return 0
    finally:
        connection.close()
    assert row is not None
    return int(row[0])


def test_apply_is_a_closed_offline_canonical_write() -> None:
    registration = registration_for("manifest.apply")
    assert registration.effect is OperationEffect.CANONICAL_WRITE
    assert registration.repository is RepositoryRequirement.NONE
    assert registration.network is NetworkRequirement.NEVER
    assert [argument.name for argument in registration.arguments] == [
        "path",
        "expect_plan",
    ]
    assert registration.arguments[1].required is True


def test_absent_repository_requires_init_then_replan_and_converges_without_model_use(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("# Notes\n\nStable source.\n", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    repository = tmp_path / "runtime" / "repository"

    import cardine.cli.repository as repository_module

    def forbidden_model(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("model adapter was created")

    monkeypatch.setattr(repository_module.ModelAdapterRegistry, "create", forbidden_model)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)

    initial = _plan(manifest, capsys)
    assert [action["kind"] for action in initial["actions"]] == ["initialize"]
    initialized = _apply(
        manifest,
        cast(str, initial["fingerprint"]),
        capsys,
        environment=_UnreadableEnvironment(),
    )
    assert initialized["status"] == "applied"
    assert repository.is_dir()

    populate = _plan(manifest, capsys)
    assert [action["kind"] for action in populate["actions"]] == [
        "noop",
        "create_course",
        "ingest_revision",
        "rebuild_index",
    ]
    populated = _apply(
        manifest,
        cast(str, populate["fingerprint"]),
        capsys,
        environment=_UnreadableEnvironment(),
    )
    assert populated["status"] == "applied"
    assert _event_count(repository) == 2

    converged = _plan(manifest, capsys)
    before = _event_count(repository)
    receipt = _apply(
        manifest,
        cast(str, converged["fingerprint"]),
        capsys,
        environment=_UnreadableEnvironment(),
    )
    assert receipt["status"] == "converged"
    assert _event_count(repository) == before


def test_stale_expected_plan_is_retryable_and_does_not_mutate(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("first", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    initial = _plan(manifest, capsys)
    _apply(manifest, cast(str, initial["fingerprint"]), capsys)
    planned = _plan(manifest, capsys)
    repository = tmp_path / "runtime" / "repository"
    before = _event_count(repository)

    material.write_text("changed after planning", encoding="utf-8")
    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                cast(str, planned["fingerprint"]),
            )
        )
        == 4
    )
    error = _document(capsys)
    assert error["error"]["code"] == "lifecycle_plan_mismatch"
    assert error["error"]["details"]["expected_plan"] == planned["fingerprint"]
    assert _event_count(repository) == before


def test_expect_plan_requires_a_lowercase_sha256(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("notes", encoding="utf-8")
    manifest = _write_manifest(tmp_path)

    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                "A" * 64,
            )
        )
        == 2
    )
    assert _document(capsys)["error"]["code"] == "invalid_request"


def test_non_retryable_ingestion_failure_is_a_safe_operational_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("notes", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    initialization = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialization["fingerprint"]), capsys)
    population = _plan(manifest, capsys)

    def fail_ingestion(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise TextIngestionError(IngestionErrorCode.INVALID_CONTENT, "sensitive detail")

    monkeypatch.setattr(TextIngestionService, "ingest", fail_ingestion)
    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                cast(str, population["fingerprint"]),
            )
        )
        == 4
    )
    error = _document(capsys)
    assert error["error"] == {
        "code": "operational_failure",
        "message": "operation could not be completed safely",
    }


@pytest.mark.parametrize("operation", ("plan", "status", "apply"))
def test_canonical_source_integrity_failure_is_operational_not_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    operation: str,
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("notes", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    initialization = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialization["fingerprint"]), capsys)
    population = _plan(manifest, capsys)
    _apply(manifest, cast(str, population["fingerprint"]), capsys)
    converged = _plan(manifest, capsys)

    def corrupt_catalog(self: CourseSourceContent) -> object:
        del self
        raise SourceContentError(
            SourceContentErrorCode.INTEGRITY_ERROR,
            "sensitive canonical integrity detail",
        )

    monkeypatch.setattr(CourseSourceContent, "catalog", corrupt_catalog)
    arguments = ["--json", "manifest", operation, str(manifest)]
    if operation == "apply":
        arguments.extend(("--expect-plan", cast(str, converged["fingerprint"])))

    assert main(tuple(arguments)) == 4
    error = _document(capsys)
    assert error["error"] == {
        "code": "operational_failure",
        "message": "operation could not be completed safely",
    }


@pytest.mark.parametrize(
    ("mutation_name", "expected_original_events"),
    (
        ("create_course", 0),
        ("ingest_source", 1),
        ("rebuild_index", 2),
    ),
)
def test_repository_swap_before_each_mutation_is_a_conflict_and_never_writes_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    mutation_name: str,
    expected_original_events: int,
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("stable source", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    repository = tmp_path / "runtime" / "repository"
    initialize_plan = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialize_plan["fingerprint"]), capsys)
    populate_plan = _plan(manifest, capsys)

    original_mutation = getattr(LocalLifecycleRuntime, mutation_name)
    displaced = tmp_path / f"original-before-{mutation_name}"
    marker = repository / "outside-marker.txt"
    swapped = False

    def swap_then_mutate(self: LocalLifecycleRuntime, *args: object) -> None:
        nonlocal swapped
        if not swapped:
            repository.rename(displaced)
            repository.mkdir()
            marker.write_text("preserve", encoding="utf-8")
            swapped = True
        original_mutation(self, *args)

    monkeypatch.setattr(LocalLifecycleRuntime, mutation_name, swap_then_mutate)

    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                cast(str, populate_plan["fingerprint"]),
            )
        )
        == 4
    )
    error = _document(capsys)
    assert error["error"]["code"] == "lifecycle_retryable_conflict"
    assert swapped is True
    assert marker.read_text(encoding="utf-8") == "preserve"
    assert not (repository / "state" / "events.sqlite3").exists()
    assert not (repository / "state" / "retrieval.sqlite3").exists()
    assert _event_count(displaced) == expected_original_events


@pytest.mark.parametrize("swap_kind", ("root", "state"))
@pytest.mark.parametrize(
    ("target_call", "expected_original_events"),
    ((1, 0), (2, 1), (3, 2)),
)
def test_sqlite_open_after_owner_swap_stays_on_retained_state_and_conflicts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    swap_kind: str,
    target_call: int,
    expected_original_events: int,
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("stable source", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    repository = tmp_path / "runtime" / "repository"
    initialize_plan = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialize_plan["fingerprint"]), capsys)
    populate_plan = _plan(manifest, capsys)

    original_init = SQLiteEventStore.__init__
    calls = 0
    replacement_owner: Path
    if swap_kind == "root":
        original_root = tmp_path / f"retained-root-{target_call}"
        original_state = original_root / "state"
        replacement_owner = repository
        replacement_state = repository / "state"
    else:
        original_state = repository / f"retained-state-{target_call}"
        replacement_owner = tmp_path / f"external-state-{target_call}"
        replacement_state = replacement_owner

    def swap_after_cwd_pin(
        self: SQLiteEventStore,
        database: str | Path,
        registry: EventRegistry,
        *,
        read_only: bool = False,
        connection_identity_guard: SQLiteConnectionGuard | None = None,
    ) -> None:
        nonlocal calls
        is_mutation_open = not read_only and str(database) == "events.sqlite3"
        if is_mutation_open:
            calls += 1
        if is_mutation_open and calls == target_call:
            if swap_kind == "root":
                repository.rename(original_root)
                repository.mkdir()
            else:
                (repository / "state").rename(original_state)
                replacement_owner.mkdir()
                (repository / "state").symlink_to(replacement_owner, target_is_directory=True)
            (replacement_owner / "preserve.txt").write_text("preserve", encoding="utf-8")
        original_init(
            self,
            database,
            registry,
            read_only=read_only,
            connection_identity_guard=connection_identity_guard,
        )

    monkeypatch.setattr(SQLiteEventStore, "__init__", swap_after_cwd_pin)

    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                cast(str, populate_plan["fingerprint"]),
            )
        )
        == 4
    )
    assert _document(capsys)["error"]["code"] == "lifecycle_retryable_conflict"
    assert calls == target_call
    assert (replacement_owner / "preserve.txt").read_text(encoding="utf-8") == "preserve"
    assert not (replacement_state / "events.sqlite3").exists()
    assert not (replacement_state / "runs.sqlite3").exists()
    assert not (replacement_state / "retrieval.sqlite3").exists()
    assert _database_event_count(original_state / "events.sqlite3") == (expected_original_events)


def test_persistent_same_inode_config_rewrite_after_pin_is_a_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("stable source", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    repository = tmp_path / "runtime" / "repository"
    initialize_plan = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialize_plan["fingerprint"]), capsys)
    populate_plan = _plan(manifest, capsys)

    config = repository / "study-agent.json"
    original_inode = config.stat().st_ino
    original_init = SQLiteEventStore.__init__
    changed = False

    def rewrite_config(
        self: SQLiteEventStore,
        database: str | Path,
        registry: EventRegistry,
        *,
        read_only: bool = False,
        connection_identity_guard: SQLiteConnectionGuard | None = None,
    ) -> None:
        nonlocal changed
        if not changed:
            config.write_text(
                json.dumps(
                    {
                        "schema_version": 1,
                        "model": {
                            "adapter_id": "untrusted-adapter",
                            "credential_env": None,
                            "settings": {},
                        },
                    }
                ),
                encoding="utf-8",
            )
            changed = True
        original_init(
            self,
            database,
            registry,
            read_only=read_only,
            connection_identity_guard=connection_identity_guard,
        )

    monkeypatch.setattr(SQLiteEventStore, "__init__", rewrite_config)
    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                cast(str, populate_plan["fingerprint"]),
            )
        )
        == 4
    )
    assert _document(capsys)["error"]["code"] == "lifecycle_retryable_conflict"
    assert changed is True
    assert config.stat().st_ino == original_inode
    assert _event_count(repository) == 0


@pytest.mark.parametrize("database_name", ("events", "runs", "retrieval"))
@pytest.mark.parametrize("attack", ("symlink", "regular_replacement"))
def test_sqlite_connection_is_bound_before_first_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    database_name: str,
    attack: str,
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("stable source", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    repository = tmp_path / "runtime" / "repository"
    initialize_plan = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialize_plan["fingerprint"]), capsys)
    populate_plan = _plan(manifest, capsys)

    filename = f"{database_name}.sqlite3"
    entry = repository / "state" / filename
    retained = repository / "state" / f"{filename}.retained"
    external = tmp_path / f"external-{filename}"
    payload = b"external file must remain byte-for-byte unchanged"
    external.write_bytes(payload)
    original_connect = cast(Callable[..., sqlite3.Connection], sqlite3.connect)
    attacked = False

    def swapping_connect(
        database: object, *args: Any, **kwargs: Any
    ) -> sqlite3.Connection:
        nonlocal attacked
        is_guarded_target = (
            not attacked
            and str(database).startswith(f"file:{filename}?")
            and "nofollow=1" in str(database)
        )
        if not is_guarded_target:
            return original_connect(database, *args, **kwargs)
        attacked = True
        entry.rename(retained)
        if attack == "symlink":
            entry.symlink_to(external)
            return original_connect(database, *args, **kwargs)

        entry.write_bytes(payload)
        connection = original_connect(database, *args, **kwargs)
        entry.rename(external)
        retained.rename(entry)
        return connection

    monkeypatch.setattr(sqlite3, "connect", swapping_connect)
    assert (
        main(
            (
                "--json",
                "manifest",
                "apply",
                str(manifest),
                "--expect-plan",
                cast(str, populate_plan["fingerprint"]),
            )
        )
        == 4
    )
    assert _document(capsys)["error"]["code"] == "lifecycle_retryable_conflict"
    assert attacked is True

    if attack == "symlink":
        entry.unlink()
        retained.rename(entry)
    assert external.read_bytes() == payload


@pytest.mark.parametrize("adapter_failure", (False, True))
def test_lifecycle_mutation_restores_callers_working_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    adapter_failure: bool,
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("stable source", encoding="utf-8")
    manifest = _write_manifest(tmp_path)
    initialize_plan = _plan(manifest, capsys)
    _apply(manifest, cast(str, initialize_plan["fingerprint"]), capsys)
    populate_plan = _plan(manifest, capsys)
    caller_directory = tmp_path / "caller-directory"
    caller_directory.mkdir()
    monkeypatch.chdir(caller_directory)
    before = os.stat(".")

    if adapter_failure:
        def fail_sqlite_construction(*args: object, **kwargs: object) -> None:
            del args, kwargs
            raise RuntimeError("sensitive adapter failure")

        monkeypatch.setattr(SQLiteEventStore, "__init__", fail_sqlite_construction)

    status = main(
        (
            "--json",
            "manifest",
            "apply",
            str(manifest),
            "--expect-plan",
            cast(str, populate_plan["fingerprint"]),
        )
    )
    assert status == (4 if adapter_failure else 0)
    _document(capsys)
    after = os.stat(".")
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
