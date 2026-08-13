from __future__ import annotations

import json
import socket
from collections.abc import Iterator, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.cli.main import main
from cardine.cli.registry import (
    NetworkRequirement,
    OperationEffect,
    RepositoryRequirement,
    registration_for,
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


def _write_manifest(root: Path, *, source_path: str) -> Path:
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
                                "path": source_path,
                                "title": "Notes",
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


def _tree(root: Path) -> tuple[tuple[str, int, int, int, str | None], ...]:
    rows: list[tuple[str, int, int, int, str | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        payload = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_size,
                metadata.st_mtime_ns,
                payload,
            )
        )
    return tuple(rows)


def _forbid_external_composition(monkeypatch: pytest.MonkeyPatch) -> None:
    import cardine.cli.repository as repository_module

    def forbidden(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("repository or model composition was touched")

    monkeypatch.setattr(repository_module.LocalRepository, "open", forbidden)
    monkeypatch.setattr(repository_module.ModelAdapterRegistry, "create", forbidden)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)


@pytest.mark.parametrize("name", ["manifest.plan", "manifest.status"])
def test_plan_and_status_are_declared_read_only_offline_operations(name: str) -> None:
    registration = registration_for(name)
    assert registration.effect is OperationEffect.READ_ONLY
    assert registration.repository is RepositoryRequirement.NONE
    assert registration.network is NetworkRequirement.NEVER
    assert registration.arguments[0].default_json == "./study-agent.manifest.json"


def test_absent_repository_plan_and_status_are_stable_and_side_effect_free(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    material = tmp_path / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("# Notes\n\nStable source.\n", encoding="utf-8")
    manifest_path = _write_manifest(tmp_path, source_path="materials/notes.md")
    before = _tree(tmp_path)
    _forbid_external_composition(monkeypatch)

    assert (
        main(
            ("--json", "manifest", "plan", str(manifest_path)),
            environment=_UnreadableEnvironment(),
        )
        == 0
    )
    planned = _document(capsys)
    assert planned["command"] == "manifest.plan"
    plan = planned["data"]["plan"]
    assert [action["kind"] for action in plan["actions"]] == ["initialize"]
    assert plan["actions"][0]["code"] == "repository_absent"

    assert (
        main(
            ("--json", "manifest", "status", str(manifest_path)),
            environment=_UnreadableEnvironment(),
        )
        == 0
    )
    status = _document(capsys)
    assert status["command"] == "manifest.status"
    assert status["data"]["status"]["kind"] == "canonical_drift"
    assert status["data"]["plan"] == plan
    assert _tree(tmp_path) == before
    assert not (tmp_path / "runtime" / "repository").exists()


def test_converged_repository_status_does_not_mutate_canonical_or_operational_state(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    repository = tmp_path / "runtime" / "repository"
    manifest_path = _write_manifest(
        tmp_path,
        source_path="runtime/repository/materials/notes.md",
    )
    assert main(("--json", "init", str(repository))) == 0
    _document(capsys)

    source = repository / "materials" / "notes.md"
    source.parent.mkdir()
    source.write_text("# Notes\n\nStable source.\n", encoding="utf-8")
    assert (
        main(
            (
                "--json",
                "--repository",
                str(repository),
                "course",
                "create",
                "--course-id",
                "course-1",
                "--title",
                "Course One",
                "--learning-goal",
                "Explain the supplied notes",
            )
        )
        == 0
    )
    _document(capsys)
    assert (
        main(
            (
                "--json",
                "--repository",
                str(repository),
                "source",
                "add",
                "course-1",
                str(source),
                "--source-id",
                "notes-1",
                "--title",
                "Notes",
                "--trust-level",
                "80",
            )
        )
        == 0
    )
    _document(capsys)
    before = _tree(tmp_path)
    _forbid_external_composition(monkeypatch)

    assert (
        main(
            ("--json", "manifest", "status", str(manifest_path)),
            environment=_UnreadableEnvironment(),
        )
        == 0
    )
    document = _document(capsys)
    assert document["data"]["status"]["kind"] == "converged"
    assert {action["kind"] for action in document["data"]["plan"]["actions"]} == {
        "noop"
    }
    assert _tree(tmp_path) == before
