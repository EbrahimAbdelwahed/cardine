from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
from hashlib import sha256
from pathlib import Path
from typing import Any, cast

import pytest

from study_agent import __version__
from cardine.cli.main import main
from study_agent.operator_skill import skill_bytes, skill_fingerprint


def _run(capsys: pytest.CaptureFixture[str], *arguments: str) -> dict[str, Any]:
    assert main(("--json", *arguments)) == 0
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    value = json.loads(captured.out)
    assert value["ok"] is True
    return cast(dict[str, Any], value)


def test_empty_directory_extracts_exact_skill_without_repository_or_network(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("operator skill extraction attempted network access")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    output = tmp_path / "agent-skills" / "study-agent-operator" / "SKILL.md"
    receipt = _run(capsys, "operator", "skill", "--output", str(output))
    assert receipt["command"] == "operator.skill"
    assert receipt["data"] == {
        "id": "study-agent-operator",
        "version": "1.0.0",
        "fingerprint": skill_fingerprint(),
        "path": str(output),
    }
    assert output.read_bytes() == skill_bytes()
    assert sha256(output.read_bytes()).hexdigest() == receipt["data"]["fingerprint"]
    assert set(tmp_path.iterdir()) == {tmp_path / "agent-skills"}

    assert _run(capsys, "operator", "skill", "--output", str(output)) == receipt


def test_blank_project_offline_operator_journey(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "study"
    described = _run(capsys, "describe")
    assert described["data"]["harness_version"] == __version__ == "0.2.0"
    assert described["data"]["operator_skill"]["fingerprint"] == skill_fingerprint()

    _run(capsys, "init", str(root))
    base = ("--repository", str(root))
    created = _run(
        capsys,
        *base,
        "course",
        "create",
        "--course-id",
        "course-anatomy",
        "--title",
        "Anatomy",
        "--learning-goal",
        "Explain the brachial plexus",
    )
    assert created["data"]["id"] == "course-anatomy"
    assert _run(capsys, *base, "course", "list")["data"]["courses"] == [
        created["data"]
    ]

    source = root / "plexus.md"
    source.write_text("The brachial plexus is formed by C5 to T1 roots.\n", encoding="utf-8")
    added = _run(
        capsys,
        *base,
        "source",
        "add",
        "course-anatomy",
        "plexus.md",
        "--source-id",
        "source-plexus",
    )
    assert added["data"]["source"]["source_id"] == "source-plexus"
    assert len(_run(capsys, *base, "source", "list", "course-anatomy")["data"]["sources"]) == 1
    assert _run(capsys, *base, "doctor")["data"]["status"] == "ok"

    session = (
        *base,
        "session",
        "start",
        "course-anatomy",
        "--session-id",
        "session-agent-1",
    )
    started = _run(capsys, *session)
    assert _run(capsys, *session) == started
    assert _run(
        capsys, *base, "session", "get", "course-anatomy", "session-agent-1"
    )["data"] == started["data"]
    assert len(_run(capsys, "tool", "list")["data"]["tools"]) == 7

    exported = _run(
        capsys,
        *base,
        "export",
        "course-anatomy",
        "--output",
        str(root / "export"),
    )
    assert len(exported["data"]["manifest_sha256"]) == 64
    assert _run(capsys, *base, "doctor")["data"]["status"] == "ok"


def test_documented_external_agent_runs_the_actual_blank_project_journey(
    tmp_path: Path,
) -> None:
    project = Path(__file__).parents[2]
    executable = tmp_path / "study-agent"
    executable.write_text(
        f"#!{sys.executable}\n"
        "from cardine.cli.main import main\n"
        "raise SystemExit(main())\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(project / "src")
    environment["STUDY_AGENT_BIN"] = str(executable.absolute())
    repository = tmp_path / "lexical-parent" / ".." / "study"
    process = subprocess.run(
        (
            sys.executable,
            str(project / "docs" / "examples" / "external_agent.py"),
            str(repository),
        ),
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
        check=False,
    )
    assert process.returncode == 0, process.stderr or process.stdout
    receipt = json.loads(process.stdout)
    assert receipt["ok"] is True
    assert receipt["command"] == "export"
    assert receipt["data"]["high_water_sequence"] > 0
    root = tmp_path / "study"
    assert (tmp_path / "study-agent-operator.md").is_file()
    assert (root / "export-1" / "manifest.json").read_bytes() == (
        root / "export-2" / "manifest.json"
    ).read_bytes()
