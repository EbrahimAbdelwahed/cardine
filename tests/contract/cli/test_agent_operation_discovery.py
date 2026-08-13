from __future__ import annotations

import json
import socket
import sqlite3
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any, cast

import pytest

from cardine.cli.main import build_parser, main

_ROOT_KEYS = {
    "contract_version",
    "harness_version",
    "repository_schema_versions",
    "offline_default",
    "commands",
    "study_tools",
    "operator_skill",
}
_COMMAND_KEYS = {
    "name",
    "version",
    "summary",
    "effect",
    "repository",
    "network",
    "idempotency",
    "retry",
    "arguments",
    "output_contract",
    "verification",
}
_ARGUMENT_KEYS = {
    "name",
    "kind",
    "value_type",
    "required",
    "repeated",
    "default_json",
    "secret",
}
_TOOL_ENTRY_KEYS = {"manifest", "fingerprint"}
_OPERATOR_SKILL_KEYS = {"id", "version", "fingerprint", "extraction_command"}
_TOOL_MANIFEST_KEYS = {
    "name",
    "version",
    "input_schema",
    "output_schema",
    "effect",
    "required_capabilities",
    "emitted_event_kinds",
    "error_codes",
    "idempotency",
}
_EFFECTS = {
    "read_only",
    "local_write",
    "canonical_write",
    "operational_write",
    "external_model",
}
_REPOSITORY_REQUIREMENTS = {"none", "optional", "required"}
_NETWORK_REQUIREMENTS = {"never", "model_only"}
_ARGUMENT_KINDS = {"positional", "option"}
_ARGUMENT_VALUE_TYPES = {"string", "path", "integer", "number", "boolean", "json"}
_TOOL_FINGERPRINTS = {
    "citation.resolve": "1b7f74005dfaee7879322edd8f60ca7892e9e20d024b1807aafac0af5ecfcb71",
    "course.get": "ccfeca393bc56a3de08abc0d91ef68a9104255a43f0d428312c46d841008934b",
    "grounding.ask": "7452676719dfcfa31f4824f45ed1d1a417dcbbb7522494522955f762850eec0e",
    "session.get_context": "ea60a58728e9d9d96c11fa3cc69bc85e73e7fcbbb7fab9ce2b7821229734d3ba",
    "session.record_note": "e4d3e7446a82e4570c67abb9da9ddab70c60e14350e412c18065bf33c1b04986",
    "source.list": "387d629a6bd69ffad34dae41a5fe1c88f2619bda4637fcfeb3be96ac21cef24b",
    "source.search": "f66b9bf4a901367ab9867efeab53bd749218e8d01f1639282300abb55b2f5c97",
}
_PARSER_INVOCATIONS = {
    "ask": ("ask", "course-1", "question"),
    "course.create": (
        "course",
        "create",
        "--title",
        "Course",
        "--learning-goal",
        "Goal",
    ),
    "course.list": ("course", "list"),
    "describe": ("describe",),
    "doctor": ("doctor",),
    "export": ("export", "course-1", "--output", "export"),
    "init": ("init", "repository"),
    "manifest.apply": ("manifest", "apply", "--expect-plan", "0" * 64),
    "manifest.plan": ("manifest", "plan"),
    "manifest.schema": ("manifest", "schema"),
    "manifest.status": ("manifest", "status"),
    "manifest.validate": ("manifest", "validate"),
    "operator.skill": ("operator", "skill", "--output", "operator-skill.md"),
    "session.list": ("session", "list", "course-1"),
    "session.get": ("session", "get", "course-1", "session-1"),
    "session.resume": ("session", "resume", "course-1", "session-1"),
    "session.start": (
        "session",
        "start",
        "course-1",
        "--session-id",
        "session-1",
    ),
    "source.add": ("source", "add", "course-1", "source.md"),
    "source.list": ("source", "list", "course-1"),
    "tool.describe": ("tool", "describe", "grounding.ask"),
    "tool.list": ("tool", "list"),
}


class _UnreadableEnvironment(Mapping[str, str]):
    def __getitem__(self, key: str) -> str:
        raise AssertionError(f"credential environment was read: {key}")

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("credential environment was enumerated")

    def __len__(self) -> int:
        raise AssertionError("credential environment length was read")


class _NoNetworkSocket(socket.socket):
    def connect(self, address: object) -> None:
        raise AssertionError(f"discovery attempted a socket connection: {address}")

    def connect_ex(self, address: object) -> int:
        raise AssertionError(f"discovery attempted a socket connection: {address}")


def _success_document(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.endswith("\n")
    assert captured.out.count("\n") == 1
    document = json.loads(captured.out)
    assert document["ok"] is True
    return cast(dict[str, Any], document)


def _assert_closed_manifest(manifest: Mapping[str, Any]) -> None:
    """Assert the public discovery schema, including every closed object shape."""
    assert set(manifest) == _ROOT_KEYS
    assert manifest["contract_version"] == "agent-operations@1"
    assert manifest["offline_default"] is True
    operator_skill = manifest["operator_skill"]
    assert set(operator_skill) == _OPERATOR_SKILL_KEYS
    assert operator_skill["id"] == "study-agent-operator"
    assert operator_skill["version"] == "1.0.0"
    assert operator_skill["extraction_command"] == (
        "cardine --json operator skill --output PATH"
    )
    assert len(operator_skill["fingerprint"]) == 64
    assert manifest["repository_schema_versions"] == [1]

    commands = manifest["commands"]
    command_names = [item["name"] for item in commands]
    assert command_names == sorted(_PARSER_INVOCATIONS)
    assert len(commands) <= 64
    for command in commands:
        assert set(command) == _COMMAND_KEYS
        assert command["effect"] in _EFFECTS
        assert command["repository"] in _REPOSITORY_REQUIREMENTS
        assert command["network"] in _NETWORK_REQUIREMENTS
        assert command["output_contract"] == "cli-envelope@1"
        assert len(command["arguments"]) <= 64
        for argument in command["arguments"]:
            assert set(argument) == _ARGUMENT_KEYS
            assert argument["kind"] in _ARGUMENT_KINDS
            assert argument["value_type"] in _ARGUMENT_VALUE_TYPES
            for flag in ("required", "repeated", "secret"):
                assert type(argument[flag]) is bool

    tools = manifest["study_tools"]
    tool_names = [item["manifest"]["name"] for item in tools]
    assert tool_names == sorted(_TOOL_FINGERPRINTS)
    assert {item["manifest"]["name"]: item["fingerprint"] for item in tools} == (
        _TOOL_FINGERPRINTS
    )
    for tool in tools:
        assert set(tool) == _TOOL_ENTRY_KEYS
        assert set(tool["manifest"]) == _TOOL_MANIFEST_KEYS


def test_describe_has_the_exact_closed_contract_and_stable_order(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(("--json", "describe")) == 0
    manifest = _success_document(capsys)["data"]
    _assert_closed_manifest(manifest)

    assert main(("--json", "describe")) == 0
    assert _success_document(capsys)["data"] == manifest


def test_describe_models_repeated_init_settings_as_cli_strings(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(("--json", "describe")) == 0
    manifest = _success_document(capsys)["data"]

    init_command = next(item for item in manifest["commands"] if item["name"] == "init")
    model_setting = next(
        item for item in init_command["arguments"] if item["name"] == "model_setting"
    )
    assert {
        "value_type": model_setting["value_type"],
        "required": model_setting["required"],
        "repeated": model_setting["repeated"],
        "default_json": model_setting["default_json"],
    } == {
        "value_type": "string",
        "required": False,
        "repeated": True,
        "default_json": [],
    }


def test_each_discovered_command_maps_to_exactly_one_parser_leaf(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(("--json", "describe")) == 0
    command_names = {
        item["name"] for item in _success_document(capsys)["data"]["commands"]
    }

    parser = build_parser()
    parsed_names = [
        parser.parse_args(arguments).command_name
        for arguments in _PARSER_INVOCATIONS.values()
    ]
    assert set(parsed_names) == command_names == set(_PARSER_INVOCATIONS)
    assert len(parsed_names) == len(set(parsed_names))

    expected_groups = {
        "course": "{create,list}",
        "manifest": "{schema,validate,plan,status,apply}",
        "operator": "{skill}",
        "session": "{list,start,get,resume}",
        "source": "{add,list}",
        "tool": "{list,describe}",
    }
    for group, choices in expected_groups.items():
        assert main(("--json", group, "--help")) == 0
        help_text = _success_document(capsys)["data"]["text"]
        assert f"cardine {group} [-h] {choices}" in help_text


@pytest.mark.parametrize(
    ("arguments", "command"),
    [
        (("describe",), "describe"),
        (("tool", "list"), "tool.list"),
        (("tool", "describe", "grounding.ask"), "tool.describe"),
    ],
)
def test_discovery_is_offline_and_side_effect_free_in_an_empty_directory(
    arguments: tuple[str, ...],
    command: str,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    before = tuple(tmp_path.iterdir())

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("discovery attempted an external side effect")

    monkeypatch.setattr(socket, "socket", _NoNetworkSocket)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)

    assert main(("--json", *arguments), environment=_UnreadableEnvironment()) == 0
    document = _success_document(capsys)
    assert document["command"] == command
    assert tuple(tmp_path.iterdir()) == before == ()


def test_tool_list_and_describe_are_consistent(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert main(("--json", "tool", "list")) == 0
    listed = _success_document(capsys)["data"]["tools"]
    assert [item["manifest"]["name"] for item in listed] == sorted(_TOOL_FINGERPRINTS)

    assert main(("--json", "tool", "describe", "grounding.ask")) == 0
    described = _success_document(capsys)["data"]["tool"]
    assert described == next(
        item for item in listed if item["manifest"]["name"] == "grounding.ask"
    )


def test_unknown_tool_is_one_safe_json_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(("--json", "tool", "describe", "not.registered")) == 3
    captured = capsys.readouterr()
    assert captured.err == ""
    assert captured.out.count("\n") == 1
    assert json.loads(captured.out) == {
        "error": {
            "code": "not_found",
            "message": "requested canonical or local resource was not found",
        },
        "ok": False,
    }
