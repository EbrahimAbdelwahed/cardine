from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

PROJECT_ROOT = Path(__file__).parents[3]


def _run(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    process_env = os.environ.copy()
    process_env["PYTHONPATH"] = str(PROJECT_ROOT / "src")
    if env is not None:
        process_env.update(env)
    return subprocess.run(
        [sys.executable, "-m", "cardine.cli", *arguments],
        cwd=PROJECT_ROOT,
        env=process_env,
        text=True,
        capture_output=True,
        check=False,
    )


def _document(process: subprocess.CompletedProcess[str]) -> dict[str, Any]:
    assert process.stderr == ""
    assert process.stdout.endswith("\n")
    assert process.stdout.count("\n") == 1
    value = json.loads(process.stdout)
    assert isinstance(value, dict)
    return value


@pytest.mark.parametrize(
    "arguments",
    [
        ("--json", "course", "create"),
        ("course", "--json", "create"),
        ("course", "create", "--json"),
    ],
)
def test_json_is_position_independent_and_parse_errors_are_one_clean_document(
    arguments: tuple[str, ...],
) -> None:
    process = _run(*arguments)

    assert process.returncode == 2
    assert _document(process) == {
        "error": {
            "code": "invalid_request",
            "message": "the following arguments are required: --title, --learning-goal",
        },
        "ok": False,
    }


def test_module_entrypoint_help_exposes_only_the_approved_surface() -> None:
    process = _run("--help", env={"NO_COLOR": "1", "TERM": "dumb"})

    assert process.returncode == 0
    assert process.stderr == ""
    assert (
        "{init,course,source,ask,session,export,doctor,operator,manifest,describe,tool}"
        in process.stdout
    )
    for forbidden in ("principal", "capability", "execution-context", "provider", "api-key"):
        assert forbidden not in process.stdout.lower()
    assert "\x1b[" not in process.stdout


@pytest.mark.parametrize(
    "flag",
    ["--principal", "--principal-kind", "--capability", "--execution-context"],
)
def test_host_authority_cannot_be_supplied_as_a_cli_flag(flag: str, tmp_path: Path) -> None:
    process = _run(
        "--json",
        "--repository",
        str(tmp_path),
        "doctor",
        flag,
        "service",
    )

    assert process.returncode == 2
    document = _document(process)
    assert document["ok"] is False
    assert document["error"]["code"] == "invalid_request"


def test_missing_and_malformed_repositories_fail_without_tracebacks_or_secrets(
    tmp_path: Path,
) -> None:
    missing = _run("--json", "--repository", str(tmp_path / "missing"), "doctor")
    assert missing.returncode == 4
    assert _document(missing)["error"] == {
        "code": "repository_error",
        "message": "local repository is absent or incompatible",
    }

    root = tmp_path / "malformed"
    root.mkdir()
    secret = "super-secret-provider-token"
    (root / "study-agent.json").write_text(secret, encoding="utf-8")
    malformed = _run("--json", "--repository", str(root), "doctor")
    assert malformed.returncode == 4
    rendered = malformed.stdout + malformed.stderr
    assert secret not in rendered
    assert "Traceback" not in rendered
    assert _document(malformed)["error"]["code"] == "repository_error"


def test_non_tty_empty_lists_are_successful_json_without_progress_contamination(
    tmp_path: Path,
) -> None:
    root = tmp_path / "repository"
    assert _run("--json", "init", str(root)).returncode == 0
    created = _run(
        "--repository",
        str(root),
        "course",
        "create",
        "--json",
        "--course-id",
        "course-empty",
        "--title",
        "Empty course",
        "--learning-goal",
        "Verify empty collections",
    )
    assert created.returncode == 0
    _document(created)

    for command, key in (("source", "sources"), ("session", "sessions")):
        process = _run(
            "--repository",
            str(root),
            command,
            "list",
            "course-empty",
            "--json",
            env={"NO_COLOR": "1", "TERM": "dumb"},
        )
        assert process.returncode == 0
        assert _document(process)["data"][key] == []
