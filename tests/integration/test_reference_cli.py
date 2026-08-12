from __future__ import annotations

import json
from pathlib import Path

from cardine.cli.main import main


def _json_command(capsys: object, *arguments: str) -> tuple[int, dict[str, object]]:
    code = main((*arguments, "--json"))
    captured = capsys.readouterr()  # type: ignore[attr-defined]
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    return code, json.loads(captured.out)


def test_offline_reference_commands_use_canonical_services(
    tmp_path: Path, capsys: object
) -> None:
    root = tmp_path / "repository"
    code, initialized = _json_command(capsys, "init", str(root))
    assert code == 0
    assert initialized["ok"] is True

    base = ("--repository", str(root))
    code, course = _json_command(
        capsys,
        *base,
        "course",
        "create",
        "--course-id",
        "course-anatomy",
        "--title",
        "Anatomy",
        "--language",
        "en",
        "--learning-goal",
        "Explain the brachial plexus",
    )
    assert code == 0
    assert course["data"]["id"] == "course-anatomy"  # type: ignore[index]

    source_path = root / "plexus.md"
    source_path.write_text("# Plexus\nThe brachial plexus arises from C5 to T1.\n")
    code, source = _json_command(
        capsys,
        *base,
        "source",
        "add",
        "course-anatomy",
        str(source_path),
    )
    assert code == 0
    assert source["data"]["committed"] is True  # type: ignore[index]

    code, listed = _json_command(
        capsys, *base, "source", "list", "course-anatomy"
    )
    assert code == 0
    assert len(listed["data"]["sources"]) == 1  # type: ignore[index]

    code, sessions = _json_command(
        capsys, *base, "session", "list", "course-anatomy"
    )
    assert code == 0
    assert sessions["data"]["sessions"] == []  # type: ignore[index]

    code, exported = _json_command(
        capsys,
        *base,
        "export",
        "course-anatomy",
        "--output",
        "exports/anatomy-v1",
    )
    assert code == 0
    assert exported["data"]["high_water_sequence"] == 2  # type: ignore[index]

    code, exported_v2 = _json_command(
        capsys,
        *base,
        "export",
        "course-anatomy",
        "--output",
        "exports/anatomy-v2",
        "--version",
        "2",
    )
    assert code == 0
    assert exported_v2["data"]["version"] == "2"  # type: ignore[index]
    assert len(exported_v2["data"]["manifest_sha256"]) == 64  # type: ignore[index]
    manifest = json.loads((root / "exports/anatomy-v2/manifest.json").read_text())
    assert manifest["schema_version"] == 2
    assert (root / "exports/anatomy-v2/artifacts.jsonl").read_bytes() == b""

    code, doctor = _json_command(capsys, *base, "doctor")
    assert code == 0
    assert doctor["data"]["status"] == "ok"  # type: ignore[index]


def test_json_parse_failure_is_one_safe_document(capsys: object) -> None:
    code, document = _json_command(capsys, "course", "create")
    assert code == 2
    assert document["ok"] is False
    assert document["error"]["code"] == "invalid_request"  # type: ignore[index]
