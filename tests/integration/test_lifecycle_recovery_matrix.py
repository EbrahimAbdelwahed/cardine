from __future__ import annotations

import json
import socket
from collections.abc import Iterator, Mapping
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.cli.main import main


class _UnreadableEnvironment(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"lifecycle read credential environment key {key}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("lifecycle enumerated the credential environment")

    def __len__(self) -> int:
        raise AssertionError("lifecycle read the credential environment length")


class _NoNetworkSocket(socket.socket):
    def connect(self, address: object) -> None:
        raise AssertionError(f"lifecycle attempted network access: {address}")

    def connect_ex(self, address: object) -> int:
        raise AssertionError(f"lifecycle attempted network access: {address}")


def _run(
    capsys: pytest.CaptureFixture[str], *arguments: str
) -> tuple[int, dict[str, Any]]:
    code = main((*arguments, "--json"), environment=_UnreadableEnvironment())
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    return code, cast(dict[str, Any], json.loads(captured.out))


def _write_fixture(root: Path) -> tuple[Path, Path]:
    material = root / "materials" / "notes.md"
    material.parent.mkdir()
    material.write_text("# Notes\n\nThe brachial plexus uses C5 to T1.\n", encoding="utf-8")
    manifest = root / "study-agent.manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "repository": {"path": "runtime/repository", "model": None},
                "courses": [
                    {
                        "course_id": "course-anatomy",
                        "title": "Anatomy",
                        "language": "en",
                        "exam_date": None,
                        "learning_goals": ["Explain the supplied notes"],
                        "assessment_styles": [],
                        "sources": [
                            {
                                "source_id": "source-notes",
                                "path": "materials/notes.md",
                                "title": "Anatomy notes",
                                "trust_level": 90,
                                "source_role": "course_material",
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    return manifest, material


def _plan(
    capsys: pytest.CaptureFixture[str], manifest: Path
) -> tuple[str, dict[str, Any]]:
    code, document = _run(capsys, "manifest", "plan", str(manifest))
    assert code == 0
    plan = cast(dict[str, Any], document["data"]["plan"])
    return cast(str, plan["fingerprint"]), plan


def _apply(
    capsys: pytest.CaptureFixture[str], manifest: Path, fingerprint: str
) -> tuple[int, dict[str, Any]]:
    return _run(
        capsys,
        "manifest",
        "apply",
        str(manifest),
        "--expect-plan",
        fingerprint,
    )


def _tree(root: Path) -> tuple[tuple[str, int, int, str | None], ...]:
    rows: list[tuple[str, int, int, str | None]] = []
    for path in sorted(root.rglob("*")):
        metadata = path.lstat()
        digest = sha256(path.read_bytes()).hexdigest() if path.is_file() else None
        rows.append(
            (
                path.relative_to(root).as_posix(),
                metadata.st_mode,
                metadata.st_mtime_ns,
                digest,
            )
        )
    return tuple(rows)


def test_reopen_replan_apply_converges_without_duplicate_events(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import cardine.cli.repository as repository_module

    def forbidden_model(*args: object, **kwargs: object) -> None:
        del args, kwargs
        raise AssertionError("lifecycle attempted model/provider composition")

    monkeypatch.setattr(repository_module.ModelAdapterRegistry, "create", forbidden_model)
    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    manifest, _ = _write_fixture(tmp_path)
    repository = tmp_path / "runtime" / "repository"

    absent_sha, absent_plan = _plan(capsys, manifest)
    assert [item["kind"] for item in absent_plan["actions"]] == ["initialize"]
    code, initialized = _apply(capsys, manifest, absent_sha)
    assert code == 0
    assert initialized["data"]["receipt"]["status"] == "applied"
    assert [
        item["kind"] for item in initialized["data"]["receipt"]["completed"]
    ] == ["initialize"]

    code, status = _run(capsys, "manifest", "status", str(manifest))
    assert code == 0
    assert status["data"]["status"]["kind"] == "canonical_drift"
    recovery_sha = cast(str, status["data"]["plan"]["fingerprint"])
    assert recovery_sha != absent_sha

    code, recovered = _apply(capsys, manifest, recovery_sha)
    assert code == 0
    receipt = recovered["data"]["receipt"]
    assert receipt["status"] == "applied"
    assert [item["kind"] for item in receipt["completed"]] == [
        "create_course",
        "ingest_revision",
        "rebuild_index",
    ]

    first_export = tmp_path / "first-export"
    code, exported = _run(
        capsys,
        "--repository",
        str(repository),
        "export",
        "course-anatomy",
        "--output",
        str(first_export),
    )
    assert code == 0
    first_high_water = exported["data"]["high_water_sequence"]

    converged_sha, converged_plan = _plan(capsys, manifest)
    assert {item["kind"] for item in converged_plan["actions"]} == {"noop"}
    code, converged = _apply(capsys, manifest, converged_sha)
    assert code == 0
    assert converged["data"]["receipt"]["status"] == "converged"
    assert converged["data"]["receipt"]["completed"] == []

    second_export = tmp_path / "second-export"
    code, exported_again = _run(
        capsys,
        "--repository",
        str(repository),
        "export",
        "course-anatomy",
        "--output",
        str(second_export),
    )
    assert code == 0
    assert exported_again["data"]["high_water_sequence"] == first_high_water
    assert exported_again["data"]["manifest_sha256"] == exported["data"]["manifest_sha256"]

    code, doctor = _run(capsys, "--repository", str(repository), "doctor")
    assert code == 0
    assert doctor["data"]["event_replay"] == "ok"
    assert doctor["data"]["retrieval_rebuild"] == "ok"

    code, courses = _run(capsys, "--repository", str(repository), "course", "list")
    assert code == 0
    assert [item["id"] for item in courses["data"]["courses"]] == ["course-anatomy"]
    code, sources = _run(
        capsys, "--repository", str(repository), "source", "list", "course-anatomy"
    )
    assert code == 0
    assert len(sources["data"]["sources"]) == 1
    assert sources["data"]["sources"][0]["source_id"] == "source-notes"


def test_stale_pre_interruption_sha_is_rejected_without_mutation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    manifest, _ = _write_fixture(tmp_path)
    absent_sha, _ = _plan(capsys, manifest)
    assert _apply(capsys, manifest, absent_sha)[0] == 0

    recovery_sha, recovery_plan = _plan(capsys, manifest)
    before = _tree(tmp_path)
    code, stale = _apply(capsys, manifest, absent_sha)
    assert code == 4
    assert stale["error"]["code"] == "lifecycle_plan_mismatch"
    assert stale["error"]["details"] == {
        "expected_plan": absent_sha,
        "observed_plan": recovery_sha,
    }
    assert _tree(tmp_path) == before

    fresh_sha, fresh_plan = _plan(capsys, manifest)
    assert fresh_sha == recovery_sha
    assert fresh_plan == recovery_plan
    assert _apply(capsys, manifest, fresh_sha)[0] == 0
