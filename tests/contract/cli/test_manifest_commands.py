from __future__ import annotations

import json
import os
import socket
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.cli.main import main
from study_agent.adapters.filesystem.lifecycle import (
    ManifestReadError,
    load_lifecycle_manifest,
)

FIXTURE = (
    Path(__file__).parents[3]
    / "specs"
    / "done"
    / "agent-managed-lifecycle"
    / "fixtures"
    / "manifest-v1.json"
)


class _UnreadableEnvironment(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"credential environment was read: {key}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("credential environment was enumerated")

    def __len__(self) -> int:
        raise AssertionError("credential environment length was read")


class _NoNetworkSocket(socket.socket):
    def connect(self, address: object) -> None:
        raise AssertionError(f"manifest command attempted network access: {address}")

    def connect_ex(self, address: object) -> int:
        raise AssertionError(f"manifest command attempted network access: {address}")


def _document(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    return cast(dict[str, Any], json.loads(captured.out))


def _forbid_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    import cardine.cli.repository as repository_module

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("repository or model composition was touched")

    monkeypatch.setattr(repository_module.LocalRepository, "open", forbidden)
    monkeypatch.setattr(repository_module.ModelAdapterRegistry, "create", forbidden)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)


def test_manifest_schema_is_closed_and_does_not_read_the_default_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    _forbid_effects(monkeypatch)

    def forbidden_read(path: Path) -> bytes:
        raise AssertionError(path)

    def forbidden_open(*args: object, **kwargs: object) -> int:
        raise AssertionError(f"schema attempted file access: {args!r} {kwargs!r}")

    monkeypatch.setattr(Path, "read_bytes", forbidden_read)
    monkeypatch.setattr(os, "open", forbidden_open)

    assert (
        main(("--json", "manifest", "schema"), environment=_UnreadableEnvironment())
        == 0
    )
    document = _document(capsys)
    assert document["command"] == "manifest.schema"
    schema = document["data"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == {"schema_version", "repository", "courses"}


def test_manifest_validate_defaults_exactly_to_current_directory_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "study-agent.manifest.json").write_bytes(FIXTURE.read_bytes())
    _forbid_effects(monkeypatch)

    assert main(("--json", "manifest", "validate"), environment=_UnreadableEnvironment()) == 0
    assert _document(capsys) == {
        "command": "manifest.validate",
        "data": {
            "course_count": 1,
            "manifest_fingerprint": (
                "bdcc1337312ed868c4db1859fdcfe3a7ee4093ba96539e38421bdb69bf30f1d7"
            ),
            "schema_version": 1,
            "source_count": 1,
        },
        "ok": True,
    }


def test_explicit_validate_reads_only_the_selected_manifest_and_not_declared_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    selected = tmp_path / "selected.json"
    selected.write_bytes(FIXTURE.read_bytes())
    before = {path.relative_to(tmp_path): path.stat() for path in tmp_path.rglob("*")}
    _forbid_effects(monkeypatch)

    real_open = os.open
    opened: list[Path] = []

    def recording_open(path: os.PathLike[str] | str, flags: int, *args: object) -> int:
        opened.append(Path(path))
        return real_open(path, flags, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", recording_open)
    assert (
        main(
            ("--json", "manifest", "validate", str(selected)),
            environment=_UnreadableEnvironment(),
        )
        == 0
    )
    document = _document(capsys)
    after = {path.relative_to(tmp_path): path.stat() for path in tmp_path.rglob("*")}

    assert document["ok"] is True
    assert opened == [selected]
    assert before.keys() == after.keys()
    assert all(
        (before[path].st_size, before[path].st_mtime_ns)
        == (after[path].st_size, after[path].st_mtime_ns)
        for path in before
    )
    assert not (tmp_path / "runtime" / "study-repository").exists()
    assert not (tmp_path / "materials" / "anatomy-notes.md").exists()


def test_manifest_reader_uses_nonblocking_nofollow_flags(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "manifest.json"
    selected.write_bytes(FIXTURE.read_bytes())
    real_open = os.open
    observed_flags: list[int] = []

    def recording_open(path: os.PathLike[str] | str, flags: int, *args: object) -> int:
        observed_flags.append(flags)
        return real_open(path, flags, *args)  # type: ignore[arg-type]

    monkeypatch.setattr(os, "open", recording_open)
    load_lifecycle_manifest(selected)

    assert len(observed_flags) == 1
    assert observed_flags[0] & os.O_NOFOLLOW
    assert observed_flags[0] & os.O_NONBLOCK


def test_manifest_reader_rejects_symlink_directory_fifo_and_oversize(
    tmp_path: Path,
) -> None:
    selected = tmp_path / "manifest.json"
    selected.write_bytes(FIXTURE.read_bytes())
    symlink = tmp_path / "manifest-link.json"
    symlink.symlink_to(selected)
    directory = tmp_path / "manifest-directory"
    directory.mkdir()
    fifo = tmp_path / "manifest-fifo"
    os.mkfifo(fifo)
    oversize = tmp_path / "manifest-oversize.json"
    oversize.write_bytes(b" " * (1024 * 1024 + 1))

    for unsafe in (symlink, directory, fifo, oversize):
        with pytest.raises(ManifestReadError):
            load_lifecycle_manifest(unsafe)


def test_manifest_reader_rejects_path_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "manifest.json"
    selected.write_bytes(FIXTURE.read_bytes())
    replacement = tmp_path / "replacement.json"
    replacement.write_bytes(FIXTURE.read_bytes())
    real_stat = os.stat

    def replaced_stat(
        path: os.PathLike[str] | str, *, follow_symlinks: bool = True
    ) -> os.stat_result:
        if Path(path) == selected and follow_symlinks is False:
            return real_stat(replacement, follow_symlinks=False)
        return real_stat(path, follow_symlinks=follow_symlinks)

    monkeypatch.setattr(os, "stat", replaced_stat)
    with pytest.raises(ManifestReadError, match="path changed"):
        load_lifecycle_manifest(selected)


def test_manifest_reader_rejects_content_change_during_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selected = tmp_path / "manifest.json"
    selected.write_bytes(FIXTURE.read_bytes())
    real_fstat = os.fstat
    calls = 0

    def changing_fstat(descriptor: int) -> os.stat_result:
        nonlocal calls
        calls += 1
        if calls == 2:
            with selected.open("ab") as stream:
                stream.write(b" ")
        return real_fstat(descriptor)

    monkeypatch.setattr(os, "fstat", changing_fstat)
    with pytest.raises(ManifestReadError, match="changed while"):
        load_lifecycle_manifest(selected)


def test_invalid_manifest_errors_are_machine_safe_without_input_or_secret_echo(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    secret = "super-secret-manifest-value"
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": {
                    "path": "repo",
                    "model": {
                        "adapter_id": "generic",
                        "credential_env": None,
                        "settings": {"api_key": secret},
                    },
                },
                "courses": [],
            }
        ),
        encoding="utf-8",
    )

    assert main(("--json", "manifest", "validate", str(path))) == 2
    rendered = _document(capsys)
    assert rendered["ok"] is False
    assert rendered["error"]["code"] == "invalid_request"
    assert secret not in json.dumps(rendered)
    assert str(path) not in json.dumps(rendered)


def test_declared_missing_source_and_repository_are_accepted_without_opening_them(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "manifest.json"
    path.write_bytes(FIXTURE.read_bytes())
    assert not (tmp_path / "runtime" / "study-repository").exists()
    assert not (tmp_path / "materials" / "anatomy-notes.md").exists()

    assert main(("--json", "manifest", "validate", str(path))) == 0
    assert _document(capsys)["ok"] is True
