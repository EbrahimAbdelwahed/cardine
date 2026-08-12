from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from cardine.cli.main import main
from cardine.cli.repository import LocalRepository


def _run(capsys: Any, *arguments: str) -> tuple[int, dict[str, Any]]:
    code = main((*arguments, "--json"))
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    document = json.loads(captured.out)
    assert isinstance(document, dict)
    return code, document


def _initialize_course(root: Path, capsys: Any, course_id: str = "course-a") -> None:
    assert _run(capsys, "init", str(root))[0] == 0
    code, _ = _run(
        capsys,
        "--repository",
        str(root),
        "course",
        "create",
        "--course-id",
        course_id,
        "--title",
        "Reference course",
        "--learning-goal",
        "Verify the CLI boundary",
    )
    assert code == 0


def test_source_paths_outside_repository_and_symlinks_are_rejected(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "repository"
    _initialize_course(root, capsys)
    outside = tmp_path / "outside.txt"
    outside.write_text("untrusted source", encoding="utf-8")
    symlink = root / "link.txt"
    symlink.symlink_to(outside)

    for path in (outside, symlink):
        code, document = _run(
            capsys,
            "--repository",
            str(root),
            "source",
            "add",
            "course-a",
            str(path),
        )
        assert code == 2
        assert document["error"]["code"] == "invalid_request"
        assert "untrusted source" not in json.dumps(document)


def test_relative_and_absolute_source_paths_capture_the_same_repository_bytes(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    root = tmp_path / "repository"
    _initialize_course(root, capsys)
    content = b"# Cardiology\n\nThe mitral valve separates the left chambers.\n"
    source = root / "materials" / "valves.md"
    source.parent.mkdir()
    source.write_bytes(content)
    elsewhere = tmp_path / "process-cwd"
    elsewhere.mkdir()
    (elsewhere / "materials").mkdir()
    (elsewhere / "materials" / "valves.md").write_bytes(b"wrong process bytes")
    monkeypatch.chdir(elsewhere)

    first_code, first = _run(
        capsys,
        "--repository",
        str(root),
        "source",
        "add",
        "course-a",
        "materials/valves.md",
    )
    second_code, second = _run(
        capsys,
        "--repository",
        str(root),
        "source",
        "add",
        "course-a",
        str(source),
    )

    assert first_code == second_code == 0
    assert first["data"]["status"] == "emitted"
    assert second["data"]["status"] == "idempotent"
    expected_checksum = sha256(content).hexdigest()
    for document in (first, second):
        assert document["data"]["source"]["checksum_sha256"] == expected_checksum
        assert document["data"]["source"]["byte_length"] == len(content)
    assert first["data"]["source"] == second["data"]["source"]


def test_source_add_rejects_dot_and_parent_path_components(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "repository"
    _initialize_course(root, capsys)
    (root / "notes.md").write_text("bounded source", encoding="utf-8")

    for declared in ("./notes.md", "nested/../notes.md"):
        code, document = _run(
            capsys,
            "--repository",
            str(root),
            "source",
            "add",
            "course-a",
            declared,
        )
        assert code == 2
        assert document["error"]["code"] == "invalid_request"


def test_course_isolation_export_determinism_and_doctor_replay(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "repository"
    _initialize_course(root, capsys, "course-a")
    assert _run(
        capsys,
        "--repository",
        str(root),
        "course",
        "create",
        "--course-id",
        "course-b",
        "--title",
        "Second course",
        "--learning-goal",
        "Remain isolated",
    )[0] == 0
    source = root / "a.md"
    source.write_text("# Anatomy\nThe ulna is medial in anatomical position.\n", encoding="utf-8")
    assert _run(
        capsys,
        "--repository",
        str(root),
        "source",
        "add",
        "course-a",
        str(source),
    )[0] == 0

    code, other = _run(capsys, "--repository", str(root), "source", "list", "course-b")
    assert code == 0
    assert other["data"]["sources"] == []

    first = root / "exports" / "first"
    second = root / "exports" / "second"
    for destination in (first, second):
        code, exported = _run(
            capsys,
            "--repository",
            str(root),
            "export",
            "course-a",
            "--output",
            str(destination),
        )
        assert code == 0
        assert exported["data"]["manifest_sha256"]
    first_files = {
        item.relative_to(first): item.read_bytes() for item in first.rglob("*") if item.is_file()
    }
    second_files = {
        item.relative_to(second): item.read_bytes() for item in second.rglob("*") if item.is_file()
    }
    assert first_files == second_files

    code, doctor = _run(capsys, "--repository", str(root), "doctor")
    assert code == 0
    assert doctor["data"] == {
        "course_count": 2,
        "event_replay": "ok",
        "retrieval_rebuild": "ok",
        "run_store": "ok",
        "schema_version": 1,
        "sqlite_fts5": True,
        "status": "ok",
    }


def test_unknown_course_and_session_fail_without_cross_course_disclosure(
    tmp_path: Path, capsys: Any
) -> None:
    root = tmp_path / "repository"
    _initialize_course(root, capsys, "course-a")
    assert _run(
        capsys,
        "--repository",
        str(root),
        "course",
        "create",
        "--course-id",
        "course-b",
        "--title",
        "Second course",
        "--learning-goal",
        "Remain isolated",
    )[0] == 0

    code, document = _run(
        capsys,
        "--repository",
        str(root),
        "session",
        "resume",
        "course-b",
        "session-from-course-a",
    )
    assert code == 3
    assert document["error"] == {
        "code": "not_found",
        "message": "requested canonical or local resource was not found",
    }


def test_source_commit_survives_discardable_index_failure(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    root = tmp_path / "repository"
    _initialize_course(root, capsys)
    source = root / "source.txt"
    source.write_text("The radius is lateral in anatomical position.", encoding="utf-8")

    def fail_rebuild(self: LocalRepository) -> object:
        del self
        raise RuntimeError("simulated index failure with secret-token")

    monkeypatch.setattr(LocalRepository, "rebuild_retrieval", fail_rebuild)
    code, failed = _run(
        capsys,
        "--repository",
        str(root),
        "source",
        "add",
        "course-a",
        str(source),
    )
    assert code == 4
    assert failed["error"]["code"] == "index_rebuild_failed"
    assert failed["error"]["details"]["canonical_source"]["committed"] is True
    assert "secret-token" not in json.dumps(failed)

    monkeypatch.undo()
    code, listed = _run(capsys, "--repository", str(root), "source", "list", "course-a")
    assert code == 0
    assert len(listed["data"]["sources"]) == 1


def test_unavailable_model_is_a_safe_nonzero_error_without_credential_disclosure(
    tmp_path: Path, capsys: Any, monkeypatch: Any
) -> None:
    root = tmp_path / "repository"
    credential = "provider-secret-must-not-appear"
    monkeypatch.setenv("MODEL_KEY", credential)
    code, _ = _run(
        capsys,
        "init",
        str(root),
        "--model-adapter",
        "unregistered-adapter",
        "--credential-env",
        "MODEL_KEY",
    )
    assert code == 0
    assert _run(
        capsys,
        "--repository",
        str(root),
        "course",
        "create",
        "--course-id",
        "course-a",
        "--title",
        "Reference course",
        "--learning-goal",
        "Verify model errors",
    )[0] == 0

    code, document = _run(
        capsys,
        "--repository",
        str(root),
        "ask",
        "course-a",
        "What is supported?",
        "--session-id",
        "session-missing",
        "--idempotency-key",
        "fixed-key",
    )
    assert code == 4
    rendered = json.dumps(document)
    assert document["error"] == {
        "code": "model_unavailable",
        "message": "configured model adapter is unavailable",
    }
    assert credential not in rendered
    assert "Traceback" not in rendered
