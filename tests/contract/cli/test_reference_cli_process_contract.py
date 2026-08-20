from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run(*arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    merged = os.environ.copy()
    if env:
        merged.update(env)
    return subprocess.run(
        [sys.executable, "-m", "cardine.cli", *arguments],
        check=False,
        capture_output=True,
        text=True,
        env=merged,
    )


def _document(process: subprocess.CompletedProcess[str]) -> dict[str, object]:
    return json.loads(process.stdout)


@pytest.mark.parametrize(
    "arguments",
    [
        ("--json", "course", "create"),
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
        "{init,course,source,consent,pageindex,lesson,artifact,ask,session,export,doctor,operator,manifest,describe,tool}"
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
        "forged",
    )

    assert process.returncode == 2
    document = _document(process)
    assert document["ok"] is False
    assert document["error"]["code"] == "invalid_request"


def test_json_help_is_one_success_document() -> None:
    process = _run("--json", "--help")

    assert process.returncode == 0
    assert process.stderr == ""
    document = _document(process)
    assert document["ok"] is True
    assert document["command"] == "help"
    assert "usage: cardine" in document["data"]["text"]


def test_plain_help_is_plain_text() -> None:
    process = _run("--help")

    assert process.returncode == 0
    assert process.stderr == ""
    assert process.stdout.startswith("usage: cardine")


def test_unknown_command_returns_invalid_request() -> None:
    process = _run("--json", "definitely-not-a-command")

    assert process.returncode == 2
    assert _document(process)["error"]["code"] == "invalid_request"


def test_missing_repository_is_not_found_or_repository_error(tmp_path: Path) -> None:
    process = _run("--json", "--repository", str(tmp_path / "missing"), "doctor")

    assert process.returncode == 4
    assert _document(process)["error"]["code"] == "repository_error"


def test_no_color_never_changes_json_contract() -> None:
    process = _run("--json", "describe", env={"NO_COLOR": "1", "TERM": "dumb"})

    assert process.returncode == 0
    assert process.stderr == ""
    assert _document(process)["ok"] is True
    assert "\x1b[" not in process.stdout


def test_plain_describe_is_human_readable() -> None:
    process = _run("describe")

    assert process.returncode == 0
    assert process.stderr == ""
    assert process.stdout
